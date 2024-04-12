# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the Apache License, Version 2.0
# found in the LICENSE file in the root directory of this source tree.

import random

import torch
from einops import rearrange
from torch.nn.utils.rnn import pad_sequence


def collate_data_and_cast(
    samples,
    mask_ratio_tuple,
    mask_probability,
    dtype,
    n_tokens=None,
    mask_generator=None,
    free_shapes=None,
    patch_size=14,
):
    attn_mask_lc = attn_mask_gc = None
    # samples dict_keys(['global_crops', 'global_crops_teacher', 'local_crops', 'offsets'])
    # from b (nb_crops c h w) OR b (nb_crops c p nxp) to (b nc) c p np
    # nb crops goes with batch size ie: b nc -> (b nc)
    if isinstance(samples, dict):
        B, nc, c, h, w = samples["global_crops"].size()
        coll_global_crops = rearrange(
            samples["global_crops"],
            "b nc c h w -> (b nc) c h w",
            c=c,
            b=B,
        )
        coll_local_crops = rearrange(
            samples["local_crops"],
            "b nc c h w -> (b nc) c h w",
            c=c,
            b=B,
        )
        B = nc * B  # we define a new pseudo batch size

        local_crop_len = samples["local_crop_len"]
        local_patch_pos = samples["local_patch_pos"]
        local_crop_dims = samples["local_crop_dims"]

    elif isinstance(samples[0], dict):  # on gpu and with free_shapes
        nc, c, h, w = samples[0]["global_crops"].size()

        coll_global_crops = [
            rearrange(
                el["global_crops"],
                "nc c (p1 h1) (p2 w1) -> (h1 w1 p1) nc c p2",
                p1=patch_size,
                p2=patch_size,
                c=c,
            )
            for el in samples
        ]
        """
        gc_len_list = [
            el.shape[0] // patch_size + 1  # + 1 for cls token
            for el in coll_global_crops
            for _ in range(el.shape[1])
        ]  # len=B*nc
        """
        coll_global_crops = pad_sequence(coll_global_crops, batch_first=True)
        # gc_padded_len = coll_global_crops.shape[1] // patch_size + 1
        coll_global_crops = rearrange(
            coll_global_crops, "b np nc c p -> (b nc) c p np", p=patch_size
        )
        # LOCAL CROPS
        coll_local_crops = map(
            lambda t: rearrange(t["local_crops"], "c np p -> np c p", p=patch_size),
            samples,
        )  # np = (np nc), have to put the unequal dim (np) first for pad_sequence()
        coll_local_crops = pad_sequence(coll_local_crops, batch_first=True)
        # lc_padded_len = coll_local_crops.shape[1] // patch_size + 1
        # lc_len_list = [
        #    [el // patch_size + 1 for el in el["local_crop_len"]] for el in samples
        # ]
        coll_local_crops = rearrange(
            coll_local_crops, "b np c p -> b c p np", p=patch_size
        )
        B = coll_global_crops.size(0)

    else:  # on cpu
        # list of samples of len B, each sample has (x,y), and x of len #gc of #lc
        n_global_crops = len(samples[0][0]["global_crops"])
        n_local_crops = len(samples[0][0]["local_crops"])

        coll_global_crops = [
            s[0]["global_crops"][i] for i in range(n_global_crops) for s in samples
        ]
        coll_local_crops = [
            s[0]["local_crops"][i] for i in range(n_local_crops) for s in samples
        ]

        if free_shapes:
            c = coll_global_crops[0].size(0)
            coll_global_crops = [
                rearrange(el, "c n p -> n (c p)", p=patch_size, c=c)
                for el in coll_global_crops
            ]
            coll_global_crops = pad_sequence(coll_global_crops, batch_first=True)
            coll_global_crops = rearrange(
                coll_global_crops, "b n (c p) -> b c p n", p=patch_size, c=c
            )
            # [48, 4032, 14] = (b c) n p
            coll_local_crops = pad_sequence(coll_local_crops, batch_first=True)
            coll_local_crops = rearrange(
                coll_local_crops, "(b c) n p -> b c p n", p=patch_size, c=c
            )

            local_crop_len = [s[0]["local_crop_len"] for s in samples]
            local_patch_pos = [s[0]["local_patch_pos"] for s in samples]
            local_crop_dims = [s[0]["local_crop_dims"] for s in samples]
            # if random.random() > 0.9:
            #    print("coll_global_crops", coll_global_crops.shape)
            #    print("coll_local_crops", coll_local_crops.shape)
        else:
            coll_global_crops = torch.stack(coll_global_crops)
            coll_local_crops = torch.stack(coll_local_crops)

        B = len(coll_global_crops)
    N = n_tokens
    n_samples_masked = int(B * mask_probability)
    probs = torch.linspace(*mask_ratio_tuple, n_samples_masked + 1)
    upperbound = 0
    masks_list = []
    for i in range(0, n_samples_masked):
        prob_min = probs[i]
        prob_max = probs[i + 1]
        if free_shapes:
            mask_tensor = torch.rand(
                1,
                coll_global_crops[i].shape[-1] // patch_size,
            ) < random.uniform(prob_min, prob_max)
        else:
            mask_tensor = torch.BoolTensor(
                mask_generator(int(N * random.uniform(prob_min, prob_max)))
            )
        masks_list.append(mask_tensor)
        upperbound += int(N * prob_max)
    for i in range(n_samples_masked, B):
        if free_shapes:
            masks_list.append(
                torch.BoolTensor(
                    torch.zeros(
                        1, coll_global_crops[i].shape[-1] // patch_size, dtype=bool
                    )
                )
            )
        else:
            masks_list.append(torch.BoolTensor(mask_generator(0)))

    if not free_shapes:
        random.shuffle(
            masks_list
        )  # not possible if global crops of diff sizes in batch

    collated_masks = torch.stack(masks_list).flatten(1)
    mask_indices_list = collated_masks.flatten().nonzero().flatten()

    masks_weight = (
        (1 / collated_masks.sum(-1).clamp(min=1.0))
        .unsqueeze(-1)
        .expand_as(collated_masks)[collated_masks]
    )
    if mask_indices_list.shape[0] > upperbound:
        upperbound = mask_indices_list.shape[0]

    """
    if free_shapes:  # masking attention to prohibit cross-attending across patches
        attn_mask_gc = [
            torch.block_diag(
                torch.ones((len, len)),
                torch.zeros((gc_padded_len - len, gc_padded_len - len)),
            )
            if gc_padded_len > len
            else torch.ones((len, len))
            for len in gc_len_list
        ]
        attn_mask_gc = torch.stack(attn_mask_gc).bool()

        attn_mask_lc = []
        for lc_lens in lc_len_list:
            A = [torch.ones((el, el)) for el in lc_lens]
            pad_len = lc_padded_len - sum(lc_lens)
            pad_border = torch.zeros((pad_len, pad_len))
            attn_mask_lc.append(torch.block_diag(*A, pad_border))
        attn_mask_lc = torch.stack(attn_mask_lc).bool()
        '''
        attn_mask = rearrange(batched_image_ids, "b i -> b 1 i 1") == rearrange(
            batched_image_ids, "b j -> b 1 1 j"
        )
        attn_mask = attn_mask & rearrange(key_pad_mask, "b j -> b 1 1 j")
        '''
    else:
        attn_mask_lc = attn_mask_gc = None
    """
    return {
        "collated_global_crops": coll_global_crops.to(dtype),
        "collated_local_crops": coll_local_crops.to(dtype),
        "collated_masks": collated_masks,
        "mask_indices_list": mask_indices_list,
        "masks_weight": masks_weight,
        "upperbound": upperbound,
        "n_masked_patches": torch.full(
            (1,), fill_value=mask_indices_list.shape[0], dtype=torch.long
        ),
        "attn_mask_gc": attn_mask_gc,
        "attn_mask_lc": attn_mask_lc,
        "local_crop_len": local_crop_len,
        "local_patch_pos": local_patch_pos,
        "local_crop_dims": local_crop_dims,
    }
