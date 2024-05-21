import glob
import os
from enum import Enum
from typing import Optional

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
        return self._entries[index]["image"]

    def get_target(self, index: int) -> Optional[Target]:
        if self.split in [_SplitLMDBDataset.TEST, _SplitLMDBDataset.ALL]:
            return None
        else:
            if self.with_targets:
                return self._entries[index]["mask"]
            else:
                return None

    @property
    def _entries_path(self) -> str:
        if self.root.endswith("TRAIN") or self.root.endswith(
            "VAL"
        ):  # if we have a single file
            return self.root + "_*"
        elif self._split.value.upper() == "ALL":
            return os.path.join(self.root, "*")
        else:
            return os.path.join(self.root, f"*-{self._split.value.upper()}_*")

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
        # extra_full_path = self._get_extra_full_path(extra_path)
        print(f"extra_path {extra_path}")
        # fold1/masks/fold1/masks.npy
        # fold1/images/fold1/images.npy

        mask_path = os.path.join(extra_path, "fold*", "masks", "fold*", "masks.npy")
        file_list_labels = sorted(glob.glob(mask_path))

        mask_path = os.path.join(extra_path, "fold*", "masks", "fold*", "masks.npy")
        file_list_imgs = sorted(glob.glob(mask_path))

        print(f"Datasets labels file list: {file_list_labels}")
        print(f"Datasets imgs file list: {file_list_imgs}")

        accumulated = []
        if self.do_short_run:
            file_list_labels = file_list_labels[:1]
            file_list_imgs = file_list_imgs[:1]

        for image, mask in zip(file_list_imgs, file_list_labels):
            accumulated.append({"mask": mask, "image": image})

        self._entries = accumulated

    def __len__(self) -> int:
        entries = self._get_entries()
        return len(entries)

    def close(self):
        for handle in self.lmdb_handles.values():
            handle.close()
