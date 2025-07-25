import sys
from copy import deepcopy
from pathlib import Path

import pytest
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from transformers import (
    AutoTokenizer,
    BambaConfig,
    BambaForCausalLM,
    DataCollatorForSeq2Seq,
    LlamaConfig,
    LlamaForCausalLM,
)

# HACK for being able to load the collator without needing to install open-instruct
open_instruct_dir = Path(__file__).parent.parent.absolute()
sys.path.append(open_instruct_dir)
from open_instruct.dataset_processor import CHAT_TEMPLATES
from open_instruct.dataset_transformation import sft_tulu_tokenize_and_truncate_v1
from open_instruct.padding_free_collator import TensorDataCollatorWithFlattening

try:
    import mamba_ssm  # noqa
    import causal_conv1d  # noqa

    mamba_and_causal_conv_available = True
except ImportError:
    mamba_and_causal_conv_available = False

try:
    import flash_attn  # noqa

    flash_attn_available = True
except ImportError:
    flash_attn_available = False

MODEL_CLASSES = {"bamba": BambaForCausalLM, "llama": LlamaForCausalLM}
MODEL_CFGS = {"bamba": BambaConfig, "llama": LlamaConfig}
MODEL_KWARGS = {
    "bamba": dict(
        attention_dropout=0.0,
        attn_layer_indices=None,
        attn_rotary_emb=8,
        hidden_act="silu",
        hidden_size=32,
        initializer_range=0.02,
        intermediate_size=64,
        mamba_chunk_size=16,
        mamba_d_conv=4,
        mamba_d_state=16,
        mamba_expand=2,
        mamba_n_groups=1,
        mamba_n_heads=16,
        max_position_embeddings=512,
        num_attention_heads=4,
        num_hidden_layers=1,
        num_key_value_heads=2,
        pad_token_id=0,
    ),
    "llama": dict(
        hidden_act="gelu",
        hidden_size=32,
        intermediate_size=64,
        is_training=True,
        max_position_embeddings=512,
        mlp_bias=False,
        num_attention_heads=2,
        num_hidden_layers=1,
        num_key_value_heads=2,
    ),
}


class TestPaddingFree:
    seqlen = 128
    batch_size = 2
    dtype = torch.bfloat16

    def get_fa2_model_and_cfg(self, model_name: str, vocab_size: int) -> nn.Module:
        model_cls = MODEL_CLASSES[model_name]
        model_cfg = MODEL_CFGS[model_name]
        model_kwargs = MODEL_KWARGS[model_name]
        cfg = model_cfg(
            **{
                **model_kwargs,
                "torch_dtype": self.dtype,
                "attn_implementation": "flash_attention_2",
                "vocab_size": vocab_size,
            }
        )
        model = model_cls(cfg).to("cuda", dtype=self.dtype)
        return model, cfg

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="Padding free tests require CUDA")
    @pytest.mark.skipif(not flash_attn_available, reason="Padding free requires flash_attn")
    @pytest.mark.parametrize("model_name", ["bamba", "llama"])
    @pytest.mark.parametrize("loss_type", ["mean", "sum"])
    def test_padding_free(self, model_name: str, loss_type: str) -> None:
        if model_name == "bamba" and not mamba_and_causal_conv_available:
            pytest.skip("bamba padding-free tests require mamba_ssm and causal_conv1d")
        torch.manual_seed(42)

        tokenizer = AutoTokenizer.from_pretrained("ibm-ai-platform/Bamba-9B-v2")
        tokenizer.add_special_tokens({"pad_token": "<pad>"})
        tokenizer.chat_template = CHAT_TEMPLATES["tulu"]
        vocab_size = len(tokenizer)

        model, cfg = self.get_fa2_model_and_cfg(model_name, vocab_size)
        model.initialize_weights()
        pf_model = deepcopy(model)

        inputs = torch.randint(cfg.vocab_size, size=(self.batch_size, self.seqlen), device="cpu")

        data = {
            0: {
                "messages": [
                    {"role": "user", "content": "Why did the chicken cross the road?"},
                    {"role": "assistant", "content": "To get to the other side"},
                ]
            },
            1: {
                "messages": [
                    {"role": "user", "content": "What is one plus two?"},
                    {"role": "assistant", "content": "The answer is 3"},
                ]
            },
        }

        tok_data = {k: sft_tulu_tokenize_and_truncate_v1(v, tokenizer, max_seq_length=2**30) for k, v in data.items()}
        for v in tok_data.values():
            del v["messages"]

        collate_fn = DataCollatorForSeq2Seq(tokenizer=tokenizer, model=model, padding="longest")
        dataloader = DataLoader(tok_data, shuffle=False, collate_fn=collate_fn, batch_size=self.batch_size)

        pf_collate_fn = TensorDataCollatorWithFlattening()
        pf_dataloader = DataLoader(tok_data, shuffle=False, collate_fn=pf_collate_fn, batch_size=self.batch_size)

        batch = next(iter(dataloader))
        pf_batch = next(iter(pf_dataloader))
        for b in (batch, pf_batch):
            for k in b:
                if torch.is_tensor(b[k]):
                    b[k] = b[k].cuda()

        assert batch["input_ids"].shape[0] == 2
        assert pf_batch["input_ids"].shape[0] == 1

        # Also create a batch with the pf style concatenation, but without the pf seq markers as a
        # control. Passing this through the model should give incorrect results.

        incorrect_pf_batch = {
            "input_ids": pf_batch["input_ids"],
            "labels": pf_batch["labels"],
            "attention_mask": torch.ones_like(pf_batch["input_ids"]),
        }

        outputs = model(**batch)
        pf_outputs = pf_model(**pf_batch)
        with torch.no_grad():
            incorrect_pf_outputs = model(**incorrect_pf_batch)

        # Compare logits (properly reshaped and masked)
        logits = outputs.logits.reshape(1, -1, outputs.logits.shape[-1])
        non_masked_logits = logits[:, batch["attention_mask"].flatten().bool()]
        pf_logits = pf_outputs.logits
        incorrect_pf_logits = incorrect_pf_outputs.logits
        torch.testing.assert_close(pf_logits, non_masked_logits)
        with pytest.raises(AssertionError, match="Mismatched elements:"):
            torch.testing.assert_close(pf_logits, incorrect_pf_logits)

        if loss_type == "mean":
            loss = outputs.loss
            pf_loss = pf_outputs.loss
        else:
            loss = F.cross_entropy(logits.view(-1, logits.size(-1)), batch["labels"].view(-1).long(), reduce="sum")
            pf_loss = F.cross_entropy(
                pf_logits.view(-1, pf_logits.size(-1)), pf_batch["labels"].view(-1).long(), reduce="sum"
            )
        torch.testing.assert_close(loss, pf_loss)

        loss.backward()
        pf_loss.backward()

        grads = {n: p.grad for n, p in model.named_parameters()}
        pf_grads = {n: p.grad for n, p in pf_model.named_parameters()}
        non_nan_grads = set()
        nan_grads = set()
        for k, g in grads.items():
            torch.testing.assert_close(g, pf_grads[k])
            non_nan_grads.add(k)
        print(f"{non_nan_grads=}")
        print(f"{nan_grads=}")
