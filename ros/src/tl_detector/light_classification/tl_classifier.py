from styx_msgs.msg import TrafficLight

import tensorflow as tf
import cv2
import random
import rospy
import scipy.misc
import model_trainer
import numpy as np
import os

class TLClassifier(object):
    def __init__(self):

        self.sess = None
        self.sess = tf.Session()

        training_mode = tf.placeholder(tf.bool)
        image_input_placeholder = tf.placeholder(tf.int8, (None, 128, 128, 3))
        image_input_layer = tf.image.convert_image_dtype(image_input_placeholder, tf.float32)

        # conv layers
        model_output = model_trainer.layers(image_input_layer, 3, training_mode)

        self.model_folder = rospy.get_param("/traffic_light_model_directory")

        saver = tf.train.Saver()
        saver.restore(self.sess, self.model_folder + "model.ckpt")

        for layer in [tensor.name for tensor in tf.get_default_graph().as_graph_def().node]:
            rospy.loginfo(str(layer))

        self.image_tensor = image_input_placeholder
        self.training_mode = training_mode
        self.output_tensor = model_output
        pass

    count = 0
    def get_classification(self, image, light_state):

        #run classifier
        image_shape = (128, 128)
        image = scipy.misc.imresize(image, image_shape)

        results = self.sess.run([tf.nn.top_k(tf.nn.softmax(self.output_tensor))],
                           {self.training_mode: False, self.image_tensor: [image]})
        detected_light_state = int(np.array(results[0].indices).flatten()[0])

        # if light_state != 4 and detected_light_state != light_state:
        #     image_id = random.randrange(0, 1000000)
        #     # save training image
        #     directory = self.model_folder + "missed/t"+str(light_state)+"/"
        #     if not os.path.exists(directory):
        #         os.makedirs(directory)
        #     image_name = directory + "image" + str(image_id) + ".jpg"
        #     cv2.imwrite(image_name, image)
        #     rospy.loginfo('savingImage incorrect:' + image_name)

        if detected_light_state == 0:
            return TrafficLight.RED
        if detected_light_state == 1:
            rospy.loginfo("Returning Yellow")
            return TrafficLight.YELLOW
        if detected_light_state == 2:
            rospy.loginfo("Returning Green")
            return TrafficLight.GREEN
        rospy.loginfo("Returning Unknown")
        return TrafficLight.UNKNOWN

    def close(self):
        self.sess.close()