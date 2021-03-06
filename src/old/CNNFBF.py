from keras.applications.inception_v3 import InceptionV3
from keras.layers import Dense, GlobalAveragePooling2D, Input
from keras.models import Model
from keras.optimizers import SGD
from keras.callbacks import ModelCheckpoint, TensorBoard, EarlyStopping
import numpy as np
from src.MultimodalDataset import MultimodalDataset
import os
import glob
import cv2
import math
import random

class CNNFBF(object):

    def __init__(self, train_root='multimodal_dataset/video/images/train',
                 test_root='multimodal_dataset/video/images/test',
                 chunk_size=450):

        self.checkpointer = ModelCheckpoint(
            filepath='checkpoints/inception.{epoch:03d}-{val_acc:.2f}.hdf5',
            verbose=2,
            monitor='val_acc',
            save_best_only=True)

        # Helper: Stop when we stop learning.
        self.early_stopper = EarlyStopping(patience=5)

        self.tensorboard = TensorBoard(log_dir='./logs/cnn')

        self.dataset = MultimodalDataset()
        self.train_root = train_root
        self.test_root = test_root
        self.chunk_size = chunk_size
        self.total_train_size = self.dataset.get_total_size(train_root, chunk_size=self.chunk_size)
        self.total_test_size = self.dataset.get_total_size(test_root, chunk_size=self.chunk_size)
        self.num_frames_per_sample = 15

    def get_model(self, num_classes, weights=None):
        base_model = InceptionV3(include_top=False)

        # add a global spatial average pooling layer
        x = base_model.output
        x = GlobalAveragePooling2D()(x)
        # let's add a fully-connected layer
        x = Dense(1024, activation='relu')(x)
        # and a logistic layer -- let's say we have 2 classes
        predictions = Dense(num_classes, activation='softmax')(x)

        # this is the model we will train
        model = Model(inputs=base_model.input, outputs=predictions)
        if weights is not None:
            model.load_weights(weights)

        model.compile(optimizer='rmsprop',
                      loss='categorical_crossentropy', metrics=['accuracy'])
        return model

    def get_training_model(self, weights='imagenet'):
        # create the base pre-trained model
        def pop(model):
            """Removes the last layer in the model.
            # Raises
                TypeError: if there are no layers in the model.
            """
            if not model.layers:
                raise TypeError('There are no layers in the model.')

            model.layers.pop()
            if not model.layers:
                model.outputs = []
                model.inbound_nodes = []
                model.outbound_nodes = []
            else:
                model.layers[-1].outbound_nodes = []
                model.outputs = [model.layers[-1].output]
                # update self.inbound_nodes
                model.inbound_nodes[0].output_tensors = model.outputs
                model.inbound_nodes[0].output_shapes = [model.outputs[0]._keras_shape]
            model.built = False

        base_model = InceptionV3(weights=weights, include_top=False)

        # add a global spatial average pooling layer
        x = base_model.output
        x = GlobalAveragePooling2D()(x)
        # let's add a fully-connected layer
        x = Dense(1024, activation='relu')(x)
        # and a logistic layer -- let's say we have 2 classes
        predictions = Dense(101, activation='softmax')(x)

        # this is the model we will train
        model = Model(inputs=base_model.input, outputs=predictions)
        #model.load_weights('checkpoints/ucf101_imagenet_1.28.hdf5')

        pop(model)
        pop(model)
        pop(model)

        x = model.output
        x = GlobalAveragePooling2D()(x)
        # let's add a fully-connected layer
        x = Dense(1024, activation='relu')(x)
        # and a logistic layer -- let's say we have 2 classes
        predictions = Dense(20, activation='softmax')(x)
        model = Model(inputs=base_model.input, outputs=predictions)
        model.summary()
        return model

    def get_top_layer_model(self, base_model):
        """Used to train just the top layers of the model."""
        # first: train only the top layers (which were randomly initialized)
        # i.e. freeze all convolutional InceptionV3 layers
        for layer in base_model.layers:
            layer.trainable = False

        # compile the model (should be done *after* setting layers to non-trainable)
        base_model.compile(optimizer='rmsprop', loss='categorical_crossentropy', metrics=['accuracy'])

        return base_model

    def get_mid_layer_model(self, model):
        """After we fine-tune the dense layers, train deeper."""
        # we chose to train the top 2 inception blocks, i.e. we will freeze
        # the first 172 layers and unfreeze the rest:
        for layer in model.layers[:172]:
            layer.trainable = False
        for layer in model.layers[172:]:
            layer.trainable = True

        # we need to recompile the model for these modifications to take effect
        # we use SGD with a low learning rate
        model.compile(
            optimizer=SGD(lr=0.001, momentum=0.9),
            loss='categorical_crossentropy',
            metrics=['accuracy'])

        return model

    def frame_generator(self, batch_x, batch_y, num_frames, total_size, cur_frame_index):
        while True:
            x = None
            y = None

            if cur_frame_index + num_frames < total_size:
                x = batch_x[cur_frame_index: cur_frame_index+num_frames]
                y = batch_y[cur_frame_index: cur_frame_index+num_frames]
            else:
                cur_frame_index = 0
            yield x, y

    def batch_generator(self, root, num_frames=10, max_frames_per_seq=450):
        act_str_arr = self.dataset.get_activities_by_index(range(1, 21))
        x = []
        y = []
        while True:
            for act_str in act_str_arr:
                path = os.path.join(root, act_str)
                all_seq = glob.glob(os.path.join(path, '*'))
                for seq_ix, seq in enumerate(sorted(all_seq)):
                    files = glob.glob(os.path.join(seq, '*.jpg'))
                    for img_ix, img in enumerate(sorted(files)):
                        if img_ix < max_frames_per_seq:
                            file_name = img.split(os.path.sep)[-1]
                            dir_downsampled = os.path.join(seq, 'downsampled')
                            full_path_downsampled = os.path.join(dir_downsampled, file_name)

                            cur_img = cv2.imread(full_path_downsampled)

                            cur_img = cur_img / 255.0

                            x.append(cur_img)

                            y.append(self.dataset.activity_dict[act_str][0])

                            if len(x) == num_frames:
                                yield np.array(x), np.eye(20)[np.array(y).astype(int)]
                                x, y = ([], [])
        # cur_activity_index = 0
        # global_index = 1
        #
        # index = list(range(global_index, global_index + batch_size))
        #
        # def get_new_batch(index):
        #
        #     x, y = self.dataset.load_or_convert_images(root, index, self.chunk_size)
        #
        #     y = np.eye(20)[y.astype(int)]
        #
        #     return x, y
        #
        # x, y = get_new_batch(index)
        #
        # cur_frame_index = 0
        # while True:
        #
        #     batch_x, batch_y = next(self.frame_generator(x[cur_activity_index], y[cur_activity_index],
        #                                                  num_frames, x.shape[1], cur_frame_index))
        #     cur_frame_index += num_frames
        #
        #     if batch_x is None and batch_y is None:
        #         cur_frame_index = 0
        #         cur_activity_index += 1
        #         if cur_activity_index == x.shape[0]:
        #             print("Global Index {}".format(global_index))
        #             global_index = global_index +1 if global_index < 20 else 1
        #             index = list(range(global_index, global_index + batch_size))
        #             x, y = get_new_batch(index)
        #             cur_activity_index = 0
        #
        #         batch_x, batch_y = next(self.frame_generator(x[cur_activity_index], y[cur_activity_index], num_frames, x.shape[1], cur_frame_index))
        #
        #
        #     yield batch_x, batch_y

    def fit(self, model, nb_epoch, generator, callbacks=[], num_frames_per_batch = 10):
        steps_per_epoch = math.ceil((self.total_train_size) / num_frames_per_batch)
        steps_per_epoch_val = math.ceil((self.total_test_size) / num_frames_per_batch)

        model.fit_generator(
            generator(self.train_root, num_frames=num_frames_per_batch),
            steps_per_epoch=steps_per_epoch,
            validation_data=generator(self.test_root, num_frames=num_frames_per_batch),
            validation_steps=steps_per_epoch_val,
            epochs=nb_epoch,
            callbacks=callbacks,
            verbose=1)
        return model

    def train_model(self, weights_file=None, num_frames_per_batch=10):
        model = self.get_training_model()

        if weights_file is None:
            print("Loading network from ImageNet weights.")
            # Get and train the top layers.
            model = self.get_top_layer_model(model)
            model = self.fit(model, 10, self.batch_generator, num_frames_per_batch=num_frames_per_batch)
        else:
            print("Loading saved model: %s." % weights_file)
            model.load_weights(weights_file)

        # Get and train the mid layers.
        model = self.get_mid_layer_model(model)
        model = self.fit(model, 1000, self.batch_generator,
                         [self.checkpointer, self.tensorboard], num_frames_per_batch=num_frames_per_batch)
        return model


if __name__ == '__main__':
    cnnfbf = CNNFBF(chunk_size=450)
    cnnfbf.train_model(num_frames_per_batch=225)