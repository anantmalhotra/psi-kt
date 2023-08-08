#!/bin/bash

#SBATCH --ntasks=1                # Number of tasks (see below)
#SBATCH --cpus-per-task=4         # Number of CPU cores per task
#SBATCH --nodes=1                 # Ensure that all cores are on one machine
#SBATCH --time=0-8:00            # Runtime in D-HH:MM
#SBATCH --partition=gpu-2080ti
#SBATCH --gres=gpu:1
#SBATCH --mem-per-gpu=32G  
#SBATCH --output=hostname_%j.out  # File to which STDOUT will be written
#SBATCH --error=hostname_%j.err   # File to which STDERR will be written
#SBATCH --mail-type=END           # Type of email notification- BEGIN,END,FAIL,ALL
#SBATCH --array=0-5

A=({DKT,DKTForgetting,AKT,HKT,HLR,PPE}) 

python exp_baseline.py \
--dataset assistment17/multi_skill --model_name ${A[$SLURM_ARRAY_TASK_ID]} --random_seed 2023 \
--epoch 500 --vcl 0 --multi_node 1 \
--train_mode ls_split_time --overfit 1000 \
--batch_size 32 --eval_batch_size 256 \
--test 1 --test_every 1 --save_every 10 --validate 1 \
--train_time_ratio 0.2 --test_time_ratio 0.2 \
--early_stop 1 \
--lr 5e-3 --lr_decay 150 --expername random_2023 \
--save_folder /mnt/qb/work/mlcolab/hzhou52/0729_new_exp2_logs
# ${A[$SLURM_ARRAY_TASK_ID]}
