from functools import partial
import tensorflow as tf
from batch_norm.batch_norm_types import *
from network.util import res_block


class Net:
    def __init__(self, sess, input_shape, labels_num, net_name, batch_norm_type, bound, reg_cf, lr):
        self.sess = sess
        self.input_shape = input_shape
        self.labels_num = labels_num
        self.net_name = net_name
        self.batch_norm_type = batch_norm_type
        self.bound = bound
        self.reg_cf = reg_cf
        self.lr = lr
        self.inputs_ph = tf.placeholder(dtype=tf.float32, shape=[None, *input_shape], name='inputs')
        self.labels_ph = tf.placeholder(dtype=tf.float32, shape=[None, labels_num], name='labels')
        self.training_ph = tf.placeholder(dtype=tf.bool, shape=None, name='training')
        self.values_dict = dict()
        self.values_dict['normed_values'] = dict()
        self.values_dict['gradients'] = dict()
        self._set_graph()
        self._set_loss()
        self._set_gradients()
        self.sess.run(tf.global_variables_initializer())

    def get_normed_keys(self):
        normed_keys = self.values_dict['normed_values']
        normed_keys = list(normed_keys)
        return normed_keys

    def get_gradient_keys(self):
        gradient_keys = self.values_dict['gradients']
        gradient_keys = list(gradient_keys)
        return gradient_keys

    def train(self, x, y):
        feed_dict = {self.inputs_ph: x, self.labels_ph: y, self.training_ph: True}
        targets = [self.train_op, self.values_dict['reg_loss'], self.values_dict['loss'], self.values_dict['acc'], self.values_dict['match'], self.values_dict['normed_values'], self.values_dict['gradients']]
        _, reg_loss, loss, acc, match, normed_values, gradients = self.sess.run(targets, feed_dict=feed_dict)
        return reg_loss, loss, acc, match, normed_values, gradients

    def test(self, x, y):
        feed_dict = {self.inputs_ph: x, self.labels_ph: y, self.training_ph: False}
        targets = [self.values_dict['reg_loss'], self.values_dict['loss'], self.values_dict['acc'], self.values_dict['match'], self.values_dict['normed_values'], self.values_dict['gradients']]
        reg_loss, loss, acc, match, normed_values, gradients = self.sess.run(targets, feed_dict=feed_dict)
        return reg_loss, loss, acc, match, normed_values, gradients

    def _get_normed_values(self, x, training):
        feed_dict = {self.inputs_ph: x, self.training_ph: training}

        normed_values = self.sess.run(self.values_dict['normed_values'], feed_dict=feed_dict)
        return normed_values

    def _get_gradients(self, x, y, training):
        feed_dict = {self.inputs_ph: x, self.labels_ph: y, self.training_ph: training}

        gradients = self.sess.run(self.values_dict['gradients'], feed_dict=feed_dict)
        return gradients

    def _set_graph(self):
        vgg_kernel_size = 3
        vgg16_conv_layers = (64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512, 'M', 512, 512, 512, 'M')
        vgg_dense_layers = (512,)

        simple_kernel_size = 5
        simple_conv_layers = (32, 'M', 64, 'M')
        simple_dense_layers = (1024,)

        if self.net_name == 'vgg16':
            self._set_layers(vgg_kernel_size, vgg16_conv_layers, vgg_dense_layers)
        elif self.net_name == 'simple':
            self._set_layers(simple_kernel_size, simple_conv_layers, simple_dense_layers)
        elif self.net_name == 'resnet':
            self._set_resnet()

    def _set_loss(self):
        predicts = self.values_dict['predicts']
        errors = - self.labels_ph * tf.log(predicts + 1e-8)
        loss = tf.reduce_mean(tf.reduce_sum(errors, 1))

        self.values_dict['loss'] = loss
        self.values_dict['reg_loss'] = tf.losses.get_regularization_loss()
        self.values_dict['loss_with_reg'] = loss + self.reg_cf * self.values_dict['reg_loss']

        match = tf.cast(tf.equal(tf.argmax(predicts, 1), tf.argmax(self.labels_ph, 1)), tf.float32)
        self.values_dict['match'] = match

        self.values_dict['acc'] = tf.reduce_mean(match)

        training_step = tf.train.get_or_create_global_step()
        # lr_decay = tf.train.exponential_decay(self.lr, training_step, decay_steps=100, decay_rate=0.9, staircase=True)

        opt = tf.train.GradientDescentOptimizer(self.lr)
        with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
            self.train_op = opt.minimize(self.values_dict['loss_with_reg'], global_step=training_step)

    def _set_resnet(self):
        bn_func, use_bias = self._get_bn_func()
        bn_func = partial(bn_func, training_ph=self.training_ph, bound=self.bound)
        store_normed = self.store_normed_values

        inputs = self.inputs_ph
        inputs = tf.layers.conv2d(inputs, 16, 3, 1, 'same', use_bias=use_bias)
        inputs, normed = bn_func('conv_0', inputs)
        store_normed('conv_0', normed)

        inputs = tf.nn.relu(inputs)

        inputs, bn_dict = res_block('res0', inputs, 16, 1, use_bias, bn_func)
        for k, v in bn_dict.items():
            store_normed(k, v)

        inputs, bn_dict = res_block('res1', inputs, 16, 1, use_bias, bn_func)
        for k, v in bn_dict.items():
            store_normed(k, v)

        inputs, bn_dict = res_block('res2', inputs, 32, 2, use_bias, bn_func)
        for k, v in bn_dict.items():
            store_normed(k, v)

        inputs, bn_dict = res_block('res3', inputs, 32, 1, use_bias, bn_func)
        for k, v in bn_dict.items():
            store_normed(k, v)

        inputs, bn_dict = res_block('res4', inputs, 64, 2, use_bias, bn_func)
        for k, v in bn_dict.items():
            store_normed(k, v)

        inputs, bn_dict = res_block('res5', inputs, 64, 1, use_bias, bn_func)
        for k, v in bn_dict.items():
            store_normed(k, v)

        inputs = tf.reduce_mean(inputs, axis=(1, 2))
        predicts = tf.layers.dense(inputs, self.labels_num, activation=tf.nn.softmax)
        self.values_dict['predicts'] = predicts

    def _set_layers(self, kernel_size, conv_layers, dense_layers):
        bn_func, use_bias = self._get_bn_func()
        store_normed = self.store_normed_values

        conv = partial(tf.layers.conv2d, kernel_size=(kernel_size, kernel_size), strides=1, padding='same', activation=None, use_bias=use_bias)
        max_pool = partial(tf.layers.max_pooling2d, pool_size=(2, 2), strides=2)

        dense = partial(tf.layers.dense, activation=None)
        last_dense = partial(tf.layers.dense, activation=tf.nn.softmax)

        inputs = self.inputs_ph

        for i, layers in enumerate(conv_layers):
            if isinstance(layers, int):
                inputs, normed = bn_func('conv_{}'.format(i), conv(inputs, layers), self.training_ph, self.bound)
                store_normed('conv_{}'.format(i), normed)
                inputs = tf.nn.relu(inputs)
            elif isinstance(layers, str) and (layers == 'M'):
                inputs = max_pool(inputs)

        inputs = tf.layers.flatten(inputs)
        for i, layers in enumerate(dense_layers):
            inputs, normed = bn_func('dense_{}'.format(i), dense(inputs, layers), self.training_ph, self.bound)
            store_normed('dense_{}'.format(i), normed)
            inputs = tf.nn.relu(inputs)

        predicts = last_dense(inputs, self.labels_num)
        self.values_dict['predicts'] = predicts

    def _set_gradients(self):
        vs = tf.trainable_variables()
        gs = tf.gradients(self.values_dict['loss_with_reg'], vs)
        for vs, gs in zip(vs, gs):
            key = vs.name.replace(':', '_')
            self.values_dict['gradients'][key] = gs

    def _get_bn_func(self):
        if self.batch_norm_type == 'none':
            return none_batch_norm, True

        if self.batch_norm_type == 'batch_norm':
            return batch_norm, False

        if self.batch_norm_type == 'rigid_batch_norm':
            return rigid_batch_norm, False

        if self.batch_norm_type == 'clipped_rigid_batch_norm':
            return clipped_rigid_batch_norm, False

        if self.batch_norm_type == 'kl_batch_norm':
            return kl_batch_norm, False

        if self.batch_norm_type == 'kl_batch':
            return kl_batch, False

    def store_normed_values(self, key, normed_values):
        self.values_dict['normed_values'][key] = normed_values


if __name__ == '__main__':
    import numpy as np
    net = Net(tf.Session(), [32, 32, 3], 100, 'resnet', 'kl_batch_norm', 2, 0.2, 0.1)
    count = 0
    for v in tf.trainable_variables():
        count += np.prod(v.get_shape())
        print(v)

    print('\n\n\nparam num:', count, '\n\n\n')

    for t in tf.get_collection(tf.GraphKeys.UPDATE_OPS):
        print(t)

    for t in tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES):
        print(t)
