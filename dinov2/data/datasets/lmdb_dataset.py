import glob
import os
from enum import Enum
from typing import Union, Optional, Tuple

import lmdb
import numpy as np

from dinov2.data.datasets import ImageNet

_TargetLMDBDataset = int


class _SplitLMDBDataset(Enum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"  # NOTE: torchvision does not support the test split
    ALL = "all"


class LMDBDataset(ImageNet):
    Target = _TargetLMDBDataset
    Split = _SplitLMDBDataset
    lmdb_handles = {}

    def get_image_data(self, index: int) -> bytes:
        entry = self._entries[index]
        lmdb_txn = self._lmdb_txns[entry["lmdb_imgs_file"]]
        image_data = lmdb_txn.get(str(entry["index"]).encode("utf-8"))
        return image_data

    def get_target(self, index: int) -> Optional[Target]:
        if self.split in [_SplitLMDBDataset.TEST, _SplitLMDBDataset.ALL]:
            return None
        else:
            entries = self._get_entries()
            class_index = entries[index]["class_id"]
            return int(class_index)

    def get_class_ids(self) -> np.ndarray:
        self._get_entries()
        return self._class_ids

    @property
    def _entries_path(self) -> str:
        if self.root.endswith("TRAIN") or self.root.endswith("VAL"):  # if we have a single file
            return self.root + "_*"
        elif self._split.value.upper() == "ALL":
            return "*"
        else:
            return f"-{self._split.value.upper()}_*"

    def _get_extra_full_path(self, extra_path: str) -> str:
        if not os.path.isdir(self.root):
            return extra_path
        else:
            return os.path.join(self.root, "*")

    def _get_entries(self) -> list:
        if self._entries is None:
            self._load_extra(self._entries_path)
        assert self._entries is not None
        return self._entries

    def _load_extra(self, extra_path: str):
        extra_full_path = self._get_extra_full_path(extra_path)
        print("extra_full_path", extra_full_path)
        file_list = glob.glob(extra_full_path)

        file_list_labels = sorted([el for el in file_list if el.endswith("labels")])
        print("Datasets labels file list: ", file_list_labels)

        file_list_imgs = sorted([el for el in file_list if el.endswith("imgs") or el.endswith("images")])
        print("Datasets imgs file list: ", file_list_imgs)

        accumulated = []
        self._lmdb_txns = dict()
        global_idx = 0

        if self.do_short_run:
            file_list_labels = file_list_labels[:1]
            file_list_imgs = file_list_imgs[:1]
        for lmdb_path_labels, lmdb_path_imgs in zip(file_list_labels, file_list_imgs):
            lmdb_env_labels = lmdb.open(lmdb_path_labels, readonly=True, lock=False, readahead=False, meminit=False)
            lmdb_env_imgs = lmdb.open(lmdb_path_imgs, readonly=True, lock=False, readahead=False, meminit=False)
            # ex: "/home/jluesch/Documents/data/plankton/lmdb/2007-TRAIN")
            print("lmdb_env_imgs.stat()", lmdb_env_imgs.stat())

            lmdb_txn_labels = lmdb_env_labels.begin()
            lmdb_txn_imgs = lmdb_env_imgs.begin()
            # save img tcxn from which to get labels later
            self._lmdb_txns[lmdb_path_imgs] = lmdb_txn_imgs

            lmdb_cursor = lmdb_txn_labels.cursor()
            for key, value in lmdb_cursor:
                entry = dict()
                entry["index"] = int(key.decode())
                entry["class_id"] = int(value.decode())
                entry["lmdb_imgs_file"] = lmdb_path_imgs

                accumulated.append(entry)
                global_idx += 1

            # free up resources
            lmdb_cursor.close()
            lmdb_env_labels.close()

        class_ids = [el["class_id"] for el in accumulated]
        print(f"#unique_class_ids: {self._split}, {len(set(class_ids))}")

        self._entries = accumulated
        self._class_ids = class_ids

    def __len__(self) -> int:
        entries = self._get_entries()
        return len(entries)

    def close(self):
        for handle in self.lmdb_handles.values():
            handle.close()
