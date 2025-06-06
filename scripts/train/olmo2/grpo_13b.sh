python mason.py \
    --cluster ai2/jupiter-cirrascale-2 \
    --workspace ai2/tulu-3-dev \
    --priority high \
    --image nathanl/open_instruct_auto --pure_docker_mode \
    --preemptible \
    --num_nodes 2 \
    --budget ai2/oe-adapt \
    --gpus 8 -- source configs/beaker_configs/ray_node_setup.sh \&\& python open_instruct/grpo_vllm_thread_ray_gtrl.py \
    --exp_name olmo2_13b_grpo \
    --beta 0.01 \
    --local_mini_batch_size 32 \
    --number_samples_per_prompt 16 \
    --local_rollout_batch_size 4 \
    --kl_estimator kl3 \
    --learning_rate 5e-7 \
    --dataset_mixer_list allenai/RLVR-GSM-MATH-IF-Mixed-Constraints 1.0 \
    --dataset_mixer_list_splits train \
    --dataset_mixer_eval_list allenai/RLVR-GSM-MATH-IF-Mixed-Constraints 16 \
    --dataset_mixer_eval_list_splits train \
    --max_token_length 2048 \
    --max_prompt_token_length 2048 \
    --response_length 2048 \
    --model_name_or_path allenai/OLMo-2-1124-13B-DPO \
    --model_revision main \
    --tokenizer_name allenai/OLMo-2-1124-13B-DPO \
    --tokenizer_revision main \
    --use_slow_tokenizer False \
    --add_bos \
    --non_stop_penalty \
    --stop_token eos \
    --temperature 1.0 \
    --ground_truths_key ground_truth \
    --chat_template_name tulu \
    --sft_messages_key messages \
    --total_episodes 2000000 \
    --penalty_reward_value 0.0 \
    --deepspeed_stage 2 \
    --per_device_train_batch_size 1 \
    --local_rollout_forward_batch_size 2 \
    --actor_num_gpus_per_node 4 8 \
    --num_epochs 1 \
    --vllm_tensor_parallel_size 4 \
    --lr_scheduler_type constant \
    --apply_verifiable_reward true \
    --seed 1 \
    --num_evals 100 \
    --save_freq 40 \
    --reward_model_multiplier 0.0 \
    --no_try_launch_beaker_eval_jobs \
    --try_launch_beaker_eval_jobs_on_weka \
    --gradient_checkpointing \
    --gather_whole_model False \
    --with_tracking