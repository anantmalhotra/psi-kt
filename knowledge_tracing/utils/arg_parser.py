def parse_args(parser):
    """
    All of the general arguments defined here.
    Model-specific arguments are defined in corresponding files.
    """
    parser.add_argument(
        "--num_learner",
        type=int,
        default=100,
        help="whether to num_learner and debug (0/>0); if num_learner>0, the number of data to train",
    )

    ############## distributed training ##############
    parser.add_argument(
        "--world_size",
        default=-1,
        type=int,
        help="number of nodes for distributed training",
    )
    parser.add_argument(
        "--rank", default=-1, type=int, help="node rank for distributed training"
    )
    parser.add_argument(
        "--dist-url",
        default="env://",
        type=str,
        help="url used to set up distributed training",
    )
    parser.add_argument(
        "--dist-backend", default="nccl", type=str, help="distributed backend"
    )
    parser.add_argument(
        "--local_rank", default=-1, type=int, help="local rank for distributed training"
    )
    parser.add_argument("--distributed", default=1, type=int, help="")

    ############## global ############## from main.py
    parser.add_argument(
        "--random_seed",
        type=int,
        default=2023,
    )
    parser.add_argument(
        "--GPU_to_use", type=int, default=None, help="GPU to use for training"
    )
    parser.add_argument("--gpu", type=str, default="0", help="Set CUDA_VISIBLE_DEVICES")

    ############## data loader ##############
    parser.add_argument(
        "--data_dir",
        type=str,
        default="../kt",
        help="Input data dir.",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        help="dataset choices from [junyi15, assistment12, asssitment17]",
    )

    parser.add_argument(
        "--max_step",
        type=int,
        default=50,
        help="max time steps per sequence in pre-processing",
    )
    parser.add_argument(
        "--regenerate_corpus",
        type=int,
        default=1,
        help="whether to regenerate the corpus based on interaction data",
    )

    parser.add_argument(
        "--train_time_ratio",
        type=float,
        default=0.2,
        help="the ratio of training data to the total data.",
    )
    parser.add_argument(
        "--val_time_ratio",
        type=float,
        default=0.2,
        help="the ratio of validation data to the total data.",
    )
    parser.add_argument(
        "--test_time_ratio",
        type=float,
        default=0.2,
        help="the ratio of testing data to the total data.",
    )

    ############## logger ##############
    parser.add_argument(
        "--create_logs", type=int, default=1, help="whether to create logs"
    )
    parser.add_argument(
        "--save_folder",
        type=str,
        default="../kt/logs",
        help="where to save the model and logs",
    )
    parser.add_argument(
        "--save_every",
        type=int,
        default=10,
        help="how often to save the model.",
    )
    parser.add_argument(
        "--expername",
        type=str,
        default="",
        help="experiment name in saving the logs",
    )

    ############## KTRunner ##############
    parser.add_argument("--vcl", type=int, default=0, help="whether to use VCL")
    parser.add_argument("--finetune", type=int, default=0, help="whether to finetune")
    parser.add_argument(
        "--train_mode",
        type=str,
        default="ls_split_time",
        help="simple_split_time"
        + "simple_split_learner"
        + "ls_split_time"
        + "ns_split_time"
        + "ns_split_learner"
        + "ln_split_time",
    )

    ############## load and save model ##############
    parser.add_argument("--start_epoch", type=int, default=0, help="start epoch")
    parser.add_argument(
        "--load", type=int, default=0, help="whether load model and continue to train"
    )
    parser.add_argument(
        "--load_folder",
        type=str,
        default="",
        help="Where to load pre-trained model if finetuning/evaluating. "
        + "Leave empty to train from scratch",
    )

    ############## training hyperparameter ##############
    parser.add_argument(
        "--lr_decay",
        type=int,
        default=5000,
        help="After how epochs to decay LR by a factor of gamma.",
    )
    parser.add_argument("--gamma", type=float, default=0.5, help="LR decay factor.")
    parser.add_argument("--epoch", type=int, default=200, help="Number of epochs.")
    parser.add_argument(
        "--early_stop", type=int, default=10, help="whether to early-stop."
    )
    parser.add_argument("--lr", type=float, default=5e-3, help="Learning rate.")
    parser.add_argument(
        "--batch_size", type=int, default=64, help="batch size during training."
    )
    parser.add_argument(
        "--eval_batch_size", type=int, default=64, help="batch size during testing."
    )
    parser.add_argument("--vcl_predict_step", type=int, default=10)
    parser.add_argument(
        "--validate", default=1, type=int, help="validate results throughout training."
    )
    parser.add_argument(
        "--test", default=1, type=int, help="test results throughout training."
    )
    parser.add_argument(
        "--test_every", type=int, default=5, help="test results throughout training."
    )

    ############## model architecture ##############
    parser.add_argument(
        "--multi_node",
        type=int,
        default=1,
        help="whether we train the model with graph",
    )
    parser.add_argument(
        "--dropout",
        type=float,
        default=0.0,
        help="dropout probability for each deep layer",
    )
    parser.add_argument(
        "--l2", type=float, default=1e-5, help="weight of l2_regularize in loss."
    )
    parser.add_argument(
        "--optimizer",
        type=str,
        default="Adam",
        help="optimizer: GD, Adam, Adagrad, Adadelta",
    )

    return parser
