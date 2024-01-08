import glob
import json
import os
from enum import Enum
from typing import Union, Optional, Tuple

import h5py
import numpy as np

from dinov2.data.datasets import ImageNet

_TargetHDF5Dataset = int

class _SplitHDF5Dataset(Enum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"  # NOTE: torchvision does not support the test split
    ALL = "all"


class HDF5Dataset(ImageNet):
    Target = Union[_TargetHDF5Dataset]
    Split = Union[_SplitHDF5Dataset]
    hdf5_handles = {}

    def get_image_data(self, index: int) -> bytes:
        entries = self._get_entries()
        entry = entries[index]
        image_relpath = entry["path"]
        hdf5_path = entry["hdf5_file"]
        hdf5_file = self.hdf5_handles[hdf5_path]
        image_data = hdf5_file[image_relpath][()]
        return image_data

    def get_target(self, index: int) -> Optional[Target]:
        entries = self._get_entries()
        class_index = entries[index]["class_id"]
        return None if self.split == _SplitHDF5Dataset.TEST else int(class_index)

    def get_class_names(self) -> np.ndarray:
        self._get_entries()
        return self._class_names

    def get_class_ids(self) -> np.ndarray:
        self._get_entries()
        return self._class_ids

    @property
    def _entries_path(self) -> str:
        if self.root.endswith('.hdf5'): # if we have a single file
            return self.root
        if self._split.value.upper() == 'ALL':
            return '-*.hdf5'
        else:
            return f"-{self._split.value.upper()}.hdf5"

    def _get_extra_full_path(self, extra_path: str) -> str:
        print(f'root: {self.root}, extra_root: {self._extra_root}, extra_path: {extra_path}')
        if extra_path is None:
            extra_path = ''
        if os.path.isfile(self.root):
            return self.root
        else:
            return os.path.join(self.root, self._extra_root + extra_path)

    def _get_entries(self) -> list:
        if self._entries is None:
            self._load_extra(self._entries_path)
        assert self._entries is not None
        return self._entries

    def _load_extra(self, extra_path: str):
        extra_full_path = self._get_extra_full_path(extra_path)
        file_list = glob.glob(extra_full_path)
        print('Datasets file list: ', file_list)

        accumulated = []
        class_ids = []
        class_names = []

        if self.do_short_run:
            file_list = file_list[:1]
        for hdf5_file in file_list:
            file = h5py.File(hdf5_file, 'r')
            self.hdf5_handles[hdf5_file] = file
            # Read the JSON string from the 'file_index' dataset
            file_index_json = file['file_index'][()]
            file_index = json.loads(file_index_json)

            # Add the HDF5 file name to each entry and accumulate the file entries
            for entry in file_index['files']:
                entry['hdf5_file'] = hdf5_file  # Add the HDF5 file name to the entry
                accumulated.append(entry)
                class_id = entry['class_id']
                class_str = entry['class_str']
                if class_id not in class_ids:
                    class_ids.append(class_id)
                    class_names.append(class_str)

                if self.do_short_run and len(class_ids) == 7:
                    break

        if self.do_short_run: # we need to rename the classes in the test case
            unique_class_ids = list(np.unique(class_ids))
            unique_class_names = list(np.unique(class_names))
            for dict1 in accumulated:
                dict1['class_id'] = unique_class_ids.index(dict1['class_id'])
                dict1['class_str'] = str(unique_class_names.index(dict1['class_str']))
        
        unique_class_ids = np.unique([el['class_id'] for el in accumulated])
        unique_class_names = np.unique([el['class_str'] for el in accumulated])
        print(f'#unique_class_ids: {self._split}, {len(unique_class_ids)}')
        print(f'#unique_class_names: {unique_class_names[:8]}, {len(unique_class_names)}')

        self._entries = accumulated
        self._class_ids = class_ids
        self._class_names = class_names

    def __len__(self) -> int:
        entries = self._get_entries()
        return len(entries)

    def close(self):
        for handle in self.hdf5_handles.values():
            handle.close()
