import math
import pickle
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from knowledge_tracing.utils import logger


class DataReader(object):
    """
    A general data reader for different KT datasets.
    This contains the basic data features and aggregates the features according to different learners.
    For specific data features, it will be defined at get_feed_dict function in each KT model class.

    Args:
        data_dir:   data folder path (without dataset prefix)
        dataset:    the name of KT dataset (the data should be in the data folder)
        k_fold:     number of k folder to do cross-validation
        max_step:   the maximum step considered during training; NOTE: it should be defined during the pre-processing process
        num_learner:    the number of learners used for num_learnerting or small-scaled training
        train_mode: the mode of training data split; it can be either "split_learner" or "split_time"
        train_time_ratio/test_time_ratio:
                    the ratio of time steps used for training/testing data split
        val_ratio:  the ratio of learners used for validation data split
    """

    def __init__(
        self,
        args: argparse.Namespace,
        logs: logger.Logger,
    ) -> None:
        self.data_dir = args.data_dir
        self.dataset = args.dataset
        # self.k_fold = args.kfold
        self.max_step = int(args.max_step)
        self.num_learner = args.num_learner

        self.train_mode = args.train_mode
        self.train_time_ratio = args.train_time_ratio
        self.test_time_ratio = args.test_time_ratio
        self.val_ratio = args.val_time_ratio

        self.args = args
        self.logs = logs

        self.inter_df = pd.read_csv(
            Path(
                self.data_dir, self.dataset, "interactions_{}.csv".format(self.max_step)
            ),
            sep="\t",
        )
        self.corpus_path = Path(
            self.data_dir, self.dataset, "Corpus_{}.pkl".format(self.max_step)
        )

    def create_corpus(self) -> None:
        """
        Create a corpus from the interaction data.

        This method processes the interaction data to create a corpus. It aggregates data
        by user, assigns problem IDs, and divides the data into train, validation, and test sets.

        Note:
            This method modifies the attributes of the class to create the corpus.

        """
        if "problem_id" not in self.inter_df.columns:
            self.inter_df["problem_id"] = self.inter_df["skill_id"]

        self.logs.write_to_log_file(
            'Reading data from "{}", dataset = "{}" '.format(
                self.data_dir, self.dataset
            )
        )

        # Aggregate by user
        user_wise_dict = dict()
        cnt, n_inters = 0, 0
        self.data_df = {
            "train": pd.DataFrame(),
            "val": pd.DataFrame(),
            "test": pd.DataFrame(),
        }

        for user, user_df in self.inter_df.groupby("user_id"):
            user_df = user_df.sort_values("timestamp", ascending=True)

            df = user_df[: self.max_step]

            # TODO current only work with binary correct
            df = df.groupby("skill_id", group_keys=False).apply(
                lambda x: x.assign(
                    num_history=np.arange(len(x)),
                    num_success=x["correct"].cumsum(),
                )
            )
            df["num_success"] = np.maximum(df["num_success"] - 1, 0)
            df["num_failure"] = df["num_history"] - df["num_success"]

            # normalize time stamp -> because we care about the relative time
            new_df = df.sort_values("timestamp", ascending=True)
            new_df["timestamp"] = new_df["timestamp"] - min(new_df["timestamp"])

            user_wise_dict[cnt] = {
                "user_id": user,  # the ID of the learner
                "skill_seq": new_df[
                    "skill_id"
                ].values.tolist(),  # the sequence of ID of the skills
                "correct_seq": [
                    round(x) for x in df["correct"]
                ],  # the sequence of the performance corresponding to the skill (binary)
                "time_seq": new_df[
                    "timestamp"
                ].values.tolist(),  # the sequence of the time stamps; it should be in an ascending order
                "problem_seq": new_df[
                    "problem_id"
                ].values.tolist(),  # the sequence of ID of the problems; NOTE: one skill can have multiple problems
                "num_history": new_df["num_history"]
                .values.astype(int)
                .tolist(),  # until the time step, the amount of interactions of this specific skill for this learner
                "num_success": new_df["num_success"]
                .values.astype(int)
                .tolist(),  # the amount of interactions where the learner succeeded
                "num_failure": new_df["num_failure"]
                .values.astype(int)
                .tolist(),  # the amount of interactions where the learner failed
            }

            cnt += 1
            n_inters += len(df)

        self.user_seq_df = pd.DataFrame.from_dict(user_wise_dict, orient="index")
        self.n_users = max(self.inter_df["user_id"]) + 1
        self.n_skills = max(self.inter_df["skill_id"]) + 1
        self.n_problems = max(self.inter_df["problem_id"]) + 1

        self.logs.write_to_log_file(
            '"n_users": {}, "n_skills": {}, "n_problems": {}, "n_interactions": {}'.format(
                self.n_users, self.n_skills, self.n_problems, n_inters
            )
        )

        self.logs.write_to_log_file("Save corpus to {}".format(self.corpus_path))
        pickle.dump(self, open(self.corpus_path, "wb"))

        #  load the ground-truth graph if available
        self.adj = self.load_ground_truth_graph()

    def load_ground_truth_graph(self) -> np.ndarray:
        """
        Load the ground truth graph if available, otherwise create an empty graph.

        Returns:
            adj (ndarray): Adjacency matrix representing the ground truth graph.
        """
        graph_path = Path(self.data_dir, self.dataset, "adj.npy")
        if graph_path.exists():
            adj = np.load(graph_path)
        else:
            adj = np.zeros((self.n_skills, self.n_skills))
        return adj

    def gen_fold_data(self, k: int) -> None:
        """
        TODO: this function is not used in the current version
        Args:
            k: select the k-th fold to run
        """
        assert k < self.k_fold
        n_examples = len(self.user_seq_df)
        fold_size = math.ceil(n_examples / self.k_fold)

        fold_begin = k * fold_size
        fold_end = min((k + 1) * fold_size, n_examples)
        self.data_df["test"] = self.user_seq_df.iloc[fold_begin:fold_end]

        residual_df = pd.concat(
            [
                self.user_seq_df.iloc[0:fold_begin],
                self.user_seq_df.iloc[fold_end:n_examples],
            ]
        )
        val_size = int(0.1 * len(residual_df))
        val_indices = np.random.choice(residual_df.index, val_size, replace=False)
        self.data_df["val"] = self.user_seq_df.iloc[val_indices]

        self.data_df["train"] = residual_df.drop(val_indices)
        self.data_df["whole"] = self.user_seq_df

    def gen_time_split_data(
        self,
        train_time_ratio,
        test_time_ratio,
        val_time_ratio,
        random_seed=2022,
        num_learner=0,
    ):
        """"""
        self.data_df = {
            "train": dict(),
            "val": dict(),
            "test": dict(),
            "whole": dict(),
        }

        n_learners = len(self.user_seq_df)

        if num_learner:
            assert num_learner * (1 + val_time_ratio) <= n_learners
            n_val_learners = int(num_learner * val_time_ratio)
            train_val_user_list = self.user_seq_df.sample(
                n=n_val_learners + num_learner, random_state=random_seed
            )
            val_user_list = train_val_user_list.sample(
                n=n_val_learners, random_state=random_seed
            )
            test_user_list = train_val_user_list.loc[
                ~train_val_user_list.index.isin(val_user_list.index)
            ]

            # test_user_list = self.user_seq_df.loc[~self.user_seq_df.index.isin(train_val_user_list.index)]
            # test_user_list = test_user_list.sample(n=num_learner, random_state=random_seed)

        else:
            train_val_user_list = self.user_seq_df
            val_user_list = self.user_seq_df.sample(
                frac=val_time_ratio, random_state=self.args.random_seed
            )
            test_user_list = self.user_seq_df.loc[
                ~self.user_seq_df.index.isin(val_user_list.index)
            ]

        n_time_steps = len(self.user_seq_df["time_seq"][0])
        train_time_size = math.ceil(n_time_steps * train_time_ratio)
        test_time_size = math.ceil(n_time_steps * test_time_ratio)
        whole_time_size = train_time_size + test_time_size

        for key in self.user_seq_df.keys():
            if key != "user_id":
                train_value = np.stack(train_val_user_list[key].values)
                test_value = np.stack(test_user_list[key].values)
                val_value = np.stack(val_user_list[key].values)

                self.data_df["train"][key] = train_value[:, :train_time_size].tolist()
                self.data_df["test"][key] = test_value[:, :whole_time_size].tolist()
                self.data_df["whole"][key] = train_value[:, :whole_time_size].tolist()
                self.data_df["val"][key] = val_value[:, :whole_time_size].tolist()
            else:
                self.data_df["train"][key] = train_val_user_list["user_id"]
                self.data_df["test"][key] = test_user_list["user_id"]
                self.data_df["whole"][key] = train_val_user_list["user_id"]
                self.data_df["val"][key] = val_user_list["user_id"]

        for key in self.data_df.keys():
            self.data_df[key] = pd.DataFrame.from_dict(
                self.data_df[key], orient="columns"
            )

    def show_columns(self) -> None:
        """
        Prints a random row of the user sequence DataFrame to show the available data columns.
        """
        # Get a random row from the user sequence DataFrame
        self.logs.write_to_log_file("Data columns:")
        self.logs.write_to_log_file(
            self.user_seq_df.iloc[np.random.randint(0, len(self.user_seq_df))]
        )

    def load_corpus(self, args: argparse.Namespace) -> None:
        """
        Load corpus from the corpus path, and split the data into k folds.

        Args:
            logs: An object to write logs to.
            args: An object that contains command-line arguments.

        Returns:
            The corpus object that contains the loaded data.
        """

        # Load the corpus object from the pickle file at the specified path.
        self.logs.write_to_log_file(f"Load corpus from {self.corpus_path}")

        with open(self.corpus_path, "rb") as f:
            corpus = pickle.load(f)

        # Check the value of the train_mode argument to determine the type of data split.
        if "split_learner" in self.train_mode:
            corpus.gen_fold_data(self.k_fold)
            self.logs.write_to_log_file("# Training mode splits LEARNER")

        elif "split_time" in self.train_mode:
            corpus.gen_time_split_data(
                args.train_time_ratio,
                args.test_time_ratio,
                args.val_time_ratio,
                args.random_seed,
                args.num_learner,
            )
            self.logs.write_to_log_file("# Training mode splits TIME")

        self.logs.write_to_log_file(
            "# Train: {}, # val: {}, # Test: {}".format(
                len(corpus.data_df["train"]),
                len(corpus.data_df["val"]),
                len(corpus.data_df["test"]),
            )
        )

        return corpus
