import io
import copy

import h5py
from PIL import Image
import torch
import torch.utils.data as data
from torch.utils.data.dataloader import default_collate

from .utils import load_annotation_data


def video_loader(video_file_path, frame_indices):
    with h5py.File(video_file_path, 'r') as f:
        video_data = f['video']

    video = []
    for i in frame_indices:
        if i < len(video_data):
            video.append(Image.open(io.BytesIO(video_data[i])))
        else:
            return video

    return video


def get_class_labels(data):
    class_labels_map = {}
    index = 0
    for class_label in data['labels']:
        class_labels_map[class_label] = index
        index += 1
    return class_labels_map


def get_video_ids_and_annotations(data, subset):
    video_ids = []
    annotations = []

    for key, value in data['database'].items():
        this_subset = value['subset']
        if this_subset == subset:
            video_ids.append(key)
            annotations.append(value['annotations'])

    return video_ids, annotations


def make_dataset(root_path, annotation_path, subset):
    data = load_annotation_data(annotation_path)
    video_ids, annotations = get_video_ids_and_annotations(data, subset)
    class_to_idx = get_class_labels(data)
    idx_to_class = {}
    for name, label in class_to_idx.items():
        idx_to_class[label] = name

    n_videos = len(video_ids)
    dataset = []
    for i in range(n_videos):
        if i % (n_videos // 5) == 0:
            print('dataset loading [{}/{}]'.format(i, len(video_ids)))

        if 'label' in annotations[i]:
            label = annotations[i]['label']
            label_id = class_to_idx[label]
        else:
            label = 'test'
            label_id = -1

        video_path = root_path / label / f'{video_ids[i]}.hdf5'
        if not video_path.exists():
            continue

        segment = annotations[i]['segment']
        if segment[1] == 1:
            continue

        frame_indices = list(range(segment[0], segment[1]))
        sample = {
            'video': video_path,
            'segment': segment,
            'frame_indices': frame_indices,
            'video_id': video_ids[i],
            'label': label_id
        }
        dataset.append(sample)

    return dataset, idx_to_class


def collate_fn(batch):
    batch_clips, batch_targets = zip(*batch)

    if isinstance(batch_clips[0], list):
        batch_clips = [
            clip for multi_clips in batch_clips for clip in multi_clips
        ]
        batch_targets = [
            target for multi_targets in batch_targets
            for target in multi_targets
        ]

    if isinstance(batch_targets[0], int):
        return default_collate(batch_clips), default_collate(batch_targets)
    else:
        return default_collate(batch_clips), batch_targets


class VideoDataset(data.Dataset):

    def __init__(self,
                 root_path,
                 annotation_path,
                 subset,
                 spatial_transform=None,
                 temporal_transform=None,
                 target_transform=None):
        self.data, self.class_names = make_dataset(root_path, annotation_path,
                                                   subset)

        self.spatial_transform = spatial_transform
        self.temporal_transform = temporal_transform
        self.target_transform = target_transform
        self.loader = video_loader

    def loading(self, path, frame_indices):
        clip = self.loader(path, frame_indices)
        if self.spatial_transform is not None:
            self.spatial_transform.randomize_parameters()
            clip = [self.spatial_transform(img) for img in clip]
        clip = torch.stack(clip, 0).permute(1, 0, 2, 3)

        return clip

    def temporal_sliding_window(self, sample_duration, sample_stride):
        data = []
        for x in self.data:
            t_begin, t_end = x['segment']
            for t in range(t_begin, t_end, sample_stride):
                sample = copy.deepcopy(x)
                segment = (t, min(t + sample_duration, t_end))
                sample['segment'] = segment
                sample['frame_indices'] = list(range(segment[0], segment[1]))
                data.append(sample)
        self.data = data

    def __getitem__(self, index):
        path = self.data[index]['video']
        target = self.data[index]

        frame_indices = self.data[index]['frame_indices']
        if self.temporal_transform is not None:
            frame_indices = self.temporal_transform(frame_indices)

        if isinstance(frame_indices[0], list):
            clips = []
            targets = []
            for one_frame_indices in frame_indices:
                clips.append(self.loading(path, one_frame_indices))

                current_target = target
                current_target['segment'] = [
                    one_frame_indices[0], one_frame_indices[-1] + 1
                ]
                if self.target_transform is not None:
                    current_target = self.target_transform(current_target)
                targets.append(current_target)

            return clips, targets
        else:
            clip = self.loading(path, frame_indices)

            if self.target_transform is not None:
                target = self.target_transform(target)

            return clip, target

    def __len__(self):
        return len(self.data)