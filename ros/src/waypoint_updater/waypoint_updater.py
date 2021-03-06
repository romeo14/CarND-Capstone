#!/usr/bin/env python

"""The waypoint updater node."""

__author__ = "Thomas Woodside, Karthick Loganathan"

import rospy
from geometry_msgs.msg import PoseStamped, TwistStamped
from styx_msgs.msg import Lane, Waypoint
from std_msgs.msg import Int32
from tf.transformations import euler_from_quaternion

import math

'''
This node will publish waypoints from the car's current position to some `x` distance ahead.
As mentioned in the doc, you should ideally first implement a version which does not care
about traffic lights or obstacles.
Once you have created dbw_node, you will update this node to use the status of traffic lights too.
Please note that our simulator also provides the exact location of traffic lights and their
current status in `/vehicle/traffic_lights` message. You can use this message to build this node
as well as to verify your TL classifier.
TODO (for Yousuf and Aaron): Stopline location for each traffic light.
'''

LOOKAHEAD_WPS = 25  # Number of waypoints we will publish. You can change this number
KPH_MPS = 0.277778  # change from 1 KPH to 1 MPH
WAIT_TIME = 10.0
SAFE_ACCEL = 1


class WaypointUpdater(object):
    def __init__(self):
        """
        Initialize the node, states and params. Set callbacks for topics.
        """
        rospy.init_node('waypoint_updater')

        # register the subscribers
        self.curr_pose_sub = rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        self.base_wp_sub = rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)
        self.traffic_sub = rospy.Subscriber('/traffic_waypoint', Int32, self.traffic_cb)
        self.curr_vel_sub = rospy.Subscriber('/current_velocity', TwistStamped, self.velocity_cb)

        # create the publisher
        self.final_waypoints_pub = rospy.Publisher('final_waypoints', Lane, queue_size=1)

        # initialize track states
        self.base_waypoints = None
        self.pose = None
        self.last_waypoint = None
        self.traffic_wp_ind = None
        self.current_speed = 0.0

        # set parameters
        self.max_vel = rospy.get_param('~velocity', 20.0)
        self.braking_distance = self.max_vel * 2
        self.creep_speed = 5.0 * KPH_MPS
        self.stop_distance = 5.0

        # set logs and flags
        self.pose_received_time = None
        self.traffic_received_time = None
        self.apply_brake = False

        # start the loop
        self.loop()

    def __is_behind(self, pose, target_wp):
        """
        To determine if the target waypoint is behind the car
        :param pose: the pose of the car
        :param target_wp: the target waypoint
        :return: bool True if waypoint is behind else False
        """

        # get the yaw angle after transformation
        _, _, yaw = euler_from_quaternion([pose.orientation.x,
                                           pose.orientation.y,
                                           pose.orientation.z,
                                           pose.orientation.w])

        origin_x = pose.position.x
        origin_y = pose.position.y

        # shift the co-ordinates
        shift_x = target_wp.pose.pose.position.x - origin_x
        shift_y = target_wp.pose.pose.position.y - origin_y

        # rotate and check orientation
        x = (shift_x * math.cos(0.0 - yaw)) - (shift_y * math.sin(0.0 - yaw))
        if x > 0.0:
            return False
        return True

    def __get_closest_waypoint(self, pose):
        """
        Get the closest waypoint from the car's current pose
        :param pose: the pose of the car
        :return: the closest waypoint ahead of the car
        """

        num_waypoints = len(self.base_waypoints)

        # calculate the nearest waypoint by temporarily storing shortest distance
        closest_index = 0
        closest_waypoint = None
        closest_distance = float('inf')

        # check if we have a known last point; if found lopp from it to improve performance
        if self.last_waypoint:  # will happen every time except for the first time.
            for i in range(num_waypoints):
                this_index = (i + self.last_waypoint) % num_waypoints
                waypoint = self.base_waypoints[this_index]
                this_distance = self.euclidean_distance(waypoint.pose.pose.position, pose.position)
                if this_distance < closest_distance:
                    closest_index = this_index
                    closest_waypoint = waypoint
                    closest_distance = this_distance
                else:  # we are now getting further away, stop
                    break
        # iterate through all waypoints initially
        else:
            for i, waypoint in enumerate(self.base_waypoints):
                this_distance = self.euclidean_distance(waypoint.pose.pose.position, pose.position)
                if this_distance < closest_distance:
                    closest_index = i
                    closest_waypoint = waypoint
                    closest_distance = this_distance

        # check if closest waypoint is behind the car
        if self.__is_behind(pose, closest_waypoint):
            closest_index += 1

        return closest_index

    def __get_traffic_wp(self, closest_index):
        """
        To get a valid traffic waypoint index ahead of the car
        :param closest_index: the index of the waypoint near the car
        :return: the traffic waypoint index; -1 if no traffic waypoints ahead
        """
        # check if we received a traffic waypoint index
        if self.traffic_wp_ind is not None and self.traffic_wp_ind != -1:
            # check if the traffic index is ahead of the planned route
            for i in range(LOOKAHEAD_WPS):
                new_index = (i + closest_index) % len(self.base_waypoints)
                if new_index == self.traffic_wp_ind:
                    return self.traffic_wp_ind

        # not found
        return -1

    def loop(self):
        """
        Loop to publish the waypoints and recommended speed
        :return: None
        """
        # limit publishing to 30 per second
        rate = rospy.Rate(10)
        while not rospy.is_shutdown():

            # check if states are available
            if self.base_waypoints is None or self.pose is None:
                continue

            # get the current pose of the car
            pose = self.pose.pose
            header = self.pose.header

            # get the closest waypoint to the car's position
            closest_idx = self.__get_closest_waypoint(pose)
            last_index = closest_idx + LOOKAHEAD_WPS

            # get the traffic waypoint index
            traffic_wp_ind = self.__get_traffic_wp(closest_idx)

            # check if car has to brake
            if traffic_wp_ind != -1 and traffic_wp_ind is not None:
                self.apply_brake = True
            else:
                self.apply_brake = False

            # generate the next set of waypoints and set the speed for the waypoints
            next_waypoints = self.__generate_next_waypoints(pose, self.base_waypoints, closest_idx, last_index,
                                                            traffic_wp_ind)

            # get the lane object
            lane = self.__get_lane(header, next_waypoints)
            
            # publish the waypoints
            self.final_waypoints_pub.publish(lane)

            # save the last
            self.last_waypoint = closest_idx

            rate.sleep()
        else:
            rospy.logwarn("Original waypoints not yet loaded. Cannot publish final waypoints.")

    def pose_cb(self, msg):
        """
        Callback to handle pose updates. Generates the next waypoints
        :param msg: incoming message, contains pose data
        :return: None
        """
        self.pose = msg
        self.pose_received_time = rospy.Time.now()

    def __generate_next_waypoints(self, pose, waypoints, closest_idx, last_idx, traffic_idx=-1):
        """
        Method to generate and set the desired speed for the next waypoints
        :param pose: the current pose of the car
        :param waypoints: the list of target waypoints
        :param closest_idx: the waypoint close to the car
        :param last_idx: the final waypoint
        :param traffic_idx: the traffic waypoint
        :return: the list of target waypoints with smooth speed
        """

        # check if waypoints is valid
        if waypoints is None:
            return waypoints

        # get the current state
        current_speed = self.current_speed
        brake = self.apply_brake
        speed_limit = self.max_vel * KPH_MPS
        next_waypoints = []

        # if braking is not required; set incremental speeds with safe acceleration
        if not brake:
            for i in range(closest_idx, last_idx):
                wp = waypoints[i % len(self.base_waypoints)]
                current_speed = min(current_speed + SAFE_ACCEL, speed_limit)
                self.set_waypoint_velocity(wp, current_speed)
                next_waypoints.append(wp)
            return next_waypoints

        # if braking is required then validate traffic waypoint
        if traffic_idx == -1:
            rospy.logwarn("WaypointUpdater: Trying to brake while there is no traffic signal")
            return []

        # calculate the distance under which braking has to be done and the braking power
        traffic_wp = self.base_waypoints[traffic_idx % len(self.base_waypoints)]
        stop_distance = self.euclidean_distance(pose.position, traffic_wp.pose.pose.position)
        brake_coeff = min(stop_distance, self.braking_distance) / self.current_speed

        # set decremental speeds based on the distance from stop line
        traffic_idx_found = False
        for i in range(closest_idx, last_idx):
            idx = i % len(self.base_waypoints)
            wp = waypoints[idx]
            if not traffic_idx_found:
                wp_distance = self.euclidean_distance(wp.pose.pose.position, traffic_wp.pose.pose.position)
                # if traffic waypoint index set speed to 0
                if idx == traffic_idx:
                    self.set_waypoint_velocity(wp, 0)
                    traffic_idx_found = True
                # if distance is greater than mimimum stopping distance continue accelerating
                elif wp_distance > self.braking_distance:
                    current_speed = min(current_speed + SAFE_ACCEL * 0.25, speed_limit)
                    self.set_waypoint_velocity(wp, current_speed)
                else:
                    # if distance is greater than stop distance creep along
                    if wp_distance > self.stop_distance:
                        rec_speed = self.creep_speed
                    else:
                        # reduce speed based on distance from stop line
                        decelerate = brake_coeff * (self.braking_distance - wp_distance)
                        rec_speed = self.current_speed - decelerate
                    self.set_waypoint_velocity(wp, rec_speed)
            else:  # set speed as 0 for all waypoints after stop line
                self.set_waypoint_velocity(wp, 0)

            next_waypoints.append(wp)

        return next_waypoints

    def __get_lane(self, header, waypoints):
        """
        To get the lane object for publishing
        :param header: the message header
        :param waypoints: the new waypoints
        :return: the Lane object
        """
        lane = Lane()
        lane.header = header
        lane.waypoints = waypoints

        return lane

    def waypoints_cb(self, msg):
        """
        Callback to handle incoming waypoints message.
        :param msg: incoming message, contains waypoints
        :return: None
        """
        self.base_waypoints = msg.waypoints

    def traffic_cb(self, msg):
        '''
        Callback to handle incoming traffic waypoint messages
        :param msg: the incoming message containing traffic waypoint data
        :return: None
        '''
        self.traffic_wp_ind = msg.data
        self.traffic_received_time = rospy.Time.now().to_sec()

    def velocity_cb(self, msg):
        """
        Callback to set the current speed of the car
        :param msg: incoming message containing current velocity
        :return: None
        """
        # rospy.loginfo("WaypointUpdater: Received current speed {}".format(msg.twist.linear.x))
        self.current_speed = msg.twist.linear.x

    def obstacle_cb(self, msg):
        # TODO: Callback for /obstacle_waypoint message. We will implement it later
        pass

    def get_waypoint_velocity(self, waypoint):
        return waypoint.twist.twist.linear.x

    def set_waypoint_velocity(self, waypoint, velocity):
        """
        Sets the desired velocity for the waypoint. Thresholds the max value to the speed limit
        :param waypoint: the waypoint to set the speed for
        :param velocity: the desired speed
        :return: None
        """
        safe_speed = min(velocity, self.max_vel * KPH_MPS)
        waypoint.twist.twist.linear.x = safe_speed

    def distance(self, waypoints, wp1, wp2):
        dist = 0
        dl = lambda a, b: math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
        for i in range(wp1, wp2 + 1):
            dist += dl(waypoints[wp1].pose.pose.position, waypoints[i].pose.pose.position)
            wp1 = i
        return dist

    def euclidean_distance(self, p1, p2):
        """
        To calculate euclidean distance between two poses
        :param p1: the position of the first way point
        :param p2: the position of the second way point
        :return: the distance between the two poses
        """
        return math.sqrt(math.pow(p1.x - p2.x, 2) + math.pow(p1.y - p2.y, 2) + math.pow(p1.z - p2.z, 2))


if __name__ == '__main__':
    try:
        WaypointUpdater()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start waypoint updater node.')