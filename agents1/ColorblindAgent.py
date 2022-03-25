from typing import final, List, Dict, Final
import enum, random
from bw4t.BW4TBrain import BW4TBrain
from matrx.agents.agent_utils.state import State
from matrx.agents.agent_utils.navigator import Navigator
from matrx.agents.agent_utils.state_tracker import StateTracker
from matrx.actions.door_actions import OpenDoorAction
from matrx.actions.object_actions import GrabObject, DropObject
from matrx.messages.message import Message


class Phase(enum.Enum):
    PLAN_PATH_TO_CLOSED_DOOR = 1,
    FOLLOW_PATH_TO_CLOSED_DOOR = 2,
    OPEN_DOOR = 3,
    ENTER_ROOM = 4,
    TRAVERSE_ROOM = 5,
    DELIVER_ITEM = 6,
    FOLLOW_PATH_TO_DROP_OFF_LOCATION = 7,
    DROP_OBJECT = 8


class ColorblindAgent(BW4TBrain):

    def __init__(self, settings: Dict[str, object]):
        super().__init__(settings)
        self._phase = Phase.PLAN_PATH_TO_CLOSED_DOOR
        self._teamMembers = []
        self.desired_objects = []
        # only the strong agents can pick 2 blocks
        # for other agents this is 0 or 1
        self.capacity = 0
        self.drop_off_locations = []
        self.object_to_be_dropped = None
        self.initialization_flag = True

        # memory keeps track of the objects that were located but should be retrieved later
        #   it contains the following information
        #   {
        #       "visualization" : the visualization of the object that has to be picked up
        #       "location"      : the location where the object was found (TODO if a specific object is needed,
        #                           go to the nearest object with that visualization if multiple are available in this array)
        #       "drop_off_location" : where the object need to be dropped
        #   }
        self.memory = []
        self.all_rooms = []
        self.detected_objects = []
        self.processed_messages = []

    def initialize(self):
        super().initialize()
        self._state_tracker = StateTracker(agent_id=self.agent_id)
        self._navigator = Navigator(agent_id=self.agent_id,
                                    action_set=self.action_set, algorithm=Navigator.A_STAR_ALGORITHM)

    def filter_bw4t_observations(self, state):
        return state

    def decide_on_bw4t_action(self, state: State):
        agent_name = state[self.agent_id]['obj_id']
        # Add team members
        for member in state['World']['team_members']:
            if member != agent_name and member not in self._teamMembers:
                self._teamMembers.append(member)
                # Process messages from team members
        receivedMessages = self._processMessages(self._teamMembers)
        # Update trust beliefs for team members
        self._trustBlief(self._teamMembers, receivedMessages)

        # We check if we enter for first time in the method as there is recursion
        # We want to keep track of some objects and reinitialize them every time
        if self.initialization_flag:

            # Add all rooms in a list
            self.all_rooms = sorted(state.get_all_room_names())

            # Add all desired objects to a list
            desired_objects = list(map(
                lambda x: x, [wall for wall in state.values() if
                              'class_inheritance' in wall and 'GhostBlock' in wall['class_inheritance']]))
            found_obj = []
            # Will not enter here after setting the flag to False
            self.initialization_flag = False

            # Add location for every desired object
            for obj in desired_objects:
                found_obj.append(({ "shape": obj["visualization"]["shape"], "colour": None }, obj["location"]))
            self.desired_objects = sorted(found_obj, key=lambda x: x[1], reverse=True)

        while True:
            # TODO parse all new messages
            # if a desired object is found, add it to self.detected_objects list
            # if an object from detected_objects has been collected/dropped, remove it from the list
            #   AND remove the last waypoint from the navigator
            #   AND self._phase = self.previous_phase
            #   AND keep track of already dropped objects
            for msg in self.received_messages:
                if not msg in self.processed_messages and msg.from_id != self.agent_id:
                    self._parseMessage(msg)
                    self.processed_messages.append(msg)

            if len(self.detected_objects) > 0:
                self.previous_phase = self._phase
                self._phase = Phase.FOLLOW_PATH_TO_DESIRED_OBJECT
                # TODO do something

            # Phase entering room
            if Phase.ENTER_ROOM == self._phase:

                # Get the room name for the latest chosen room from the phase PLAN_PATH_TO_CLOSED_DOOR
                room = self._door['room_name']

                # Find all area tiles locations of the room to traverse
                area = list(map(
                    lambda x: x["location"],
                    [wall for wall in state.get_room_objects(room)
                     if 'class_inheritance' in wall and 'AreaTile' in wall['class_inheritance'] and
                     ("is_drop_zone" not in wall or wall['is_drop_zone'] is False)]))

                # Sort the location of the tiles and traverse them
                sorted_by_xy = sorted(sorted(area, key=lambda x: x[1]))

                # Add the locations of the tiles to traverse in order to the navigator
                self._navigator.reset_full()
                self._navigator.add_waypoints(sorted_by_xy)

                # Go to the next phase
                self._phase = Phase.TRAVERSE_ROOM

            if Phase.TRAVERSE_ROOM == self._phase:
                # Every time update the state for the new location of the agent
                self._state_tracker.update(state)

                action = self._navigator.get_move_action(self._state_tracker)
                if action != None:
                    # If the agent has moved update look for and item
                    # We are interested only in collectable items (such that can be picked)
                    object_prop = list(map(
                        lambda x: x, [wall for wall in state.get_closest_with_property("is_collectable") if
                                      wall["is_collectable"] is True and not 'GhostBlock' in wall[
                                          'class_inheritance']]))

                    # For all possible objects save only visualization and id
                    found_obj = []
                    for obj in object_prop:
                        found_obj.append((obj["visualization"], obj["obj_id"], obj["location"]))

                    # Check if some of the found objects that can be collected are desired objects
                    for obj in found_obj:
                        for des, loc in self.desired_objects:
                            if obj[0]["shape"] == des["shape"]:
                                # In case they are desired objects for the strong agent we are interested only in the
                                # first two items from bottom to up, if they are we pick them
                                # in case they are not we save them in the memory for later use
                                if ((des, loc)) in self.desired_objects and \
                                        not ((des, obj[2])) in map((lambda mem: (mem["visualization"], mem["location"])), self.memory):
                                # if ((des, loc)) != self.desired_objects[0] \
                                #         and ((des, loc)) in self.desired_objects:

                                    self._sendMessage("Found " + str(obj[0]["shape"]), self.agent_id)
                                    self.memory.append({ "visualization": { "shape": des["shape"], "colour": None }, "location": obj[2], "drop_off_location": loc })
                                    self.memory.sort(key= lambda mem: mem["location"], reverse=True)

                    # If no desired object was found just move
                    return action, {}

                # If the room is traversed go to te next room
                self._phase = Phase.PLAN_PATH_TO_CLOSED_DOOR

            # Find the path to the deliver location
            if Phase.DELIVER_ITEM == self._phase:
                locations = []
                # sort the location of the picked items so that the first dropped will be at the bottom
                for _, loc in self.drop_off_locations:
                    locations.append(loc)
                locations.sort(reverse=True)
                self._navigator.reset_full()
                # Add the navigation
                self._navigator.add_waypoints(locations)

                # Next phase
                self._phase = Phase.FOLLOW_PATH_TO_DROP_OFF_LOCATION

            # Follow path to the drop off location
            if Phase.FOLLOW_PATH_TO_DROP_OFF_LOCATION == self._phase:
                flag = False
                # Check if the current location of the agent is the correct drop off location
                for obj_id, loc in self.drop_off_locations:
                    if state[self._state_tracker.agent_id]['location'] == loc:
                        flag = True
                        self.object_to_be_dropped = obj_id
                        # if it is the correct location drop the object
                        self._phase = Phase.DROP_OBJECT
                        self.drop_off_locations.remove((obj_id, loc))

                # if not already dropped the object  move to the next location
                if not flag:
                    self._state_tracker.update(state)

                    action = self._navigator.get_move_action(self._state_tracker)
                    # Move to the next location
                    if action != None:
                        return action, {}
                    else:
                        # If dropped both items use the memory to go to the next desired object, that was found
                        # Use the traverse method phase for now and check on every step
                        # could be implemented to go to the room and then traverse it again
                        # now just checks every step
                        if len(self.memory) != 0:
                            self._navigator.reset_full()
                            self._navigator.add_waypoints([self.memory.peek()["location"]])
                            self._phase = Phase.TRAVERSE_ROOM
                        else:
                            # If memory is empty continue traversing rooms
                            self._phase = Phase.PLAN_PATH_TO_CLOSED_DOOR

                print("! DONE !")

            if Phase.DROP_OBJECT == self._phase:
                if self.object_to_be_dropped is None:
                    print("CODE BROKEN VERY BAD")
                    exit(-1)
                # update capacity
                self.capacity -= 1
                print("dropped object")
                # Drop object
                self._phase = Phase.FOLLOW_PATH_TO_DROP_OFF_LOCATION

                return DropObject.__name__, {'object_id': self.object_to_be_dropped}

            if Phase.PLAN_PATH_TO_CLOSED_DOOR == self._phase:
                self._navigator.reset_full()

                closedDoors = [door for door in state.values()
                               if 'class_inheritance' in door and 'Door' in door['class_inheritance'] and not door[
                        'is_open']]

                # Randomly pick a closed door or go to open room
                # Check if all rooms open
                if len(closedDoors) == 0:
                    # If no rooms - stuck
                    if len(self.all_rooms) == 0:
                        return None, {}
                    # get the first room, as they were sorted in the first iteration
                    room_name = self.all_rooms.pop(0)
                    # get the door of the chosen room
                    self._door = [loc for loc in state.values()
                                  if "room_name" in loc and loc['room_name'] is
                                  room_name and 'class_inheritance' in loc and
                                  'Door' in loc['class_inheritance']]

                    # in case some broken room without door - stuck
                    if len(self._door) == 0:
                        return None, {}
                    else:
                        self._door = self._door[0]

                # randomly pick closed door
                else:
                    self._door = random.choice(closedDoors)

                # get the location of the door
                doorLoc = self._door['location']

                # Location in front of door is south from door
                doorLoc = doorLoc[0], doorLoc[1] + 1

                # Send message of current action
                self._sendMessage('Moving to door of ' + self._door['room_name'], agent_name)
                self._navigator.add_waypoints([doorLoc])
                # go to the next phase
                self._phase = Phase.FOLLOW_PATH_TO_CLOSED_DOOR

            if Phase.FOLLOW_PATH_TO_CLOSED_DOOR == self._phase:
                self._state_tracker.update(state)
                # Follow path to door
                action = self._navigator.get_move_action(self._state_tracker)
                if action != None:
                    return action, {}
                # go to the next phase
                self._phase = Phase.OPEN_DOOR

            if Phase.OPEN_DOOR == self._phase:
                self._phase = Phase.ENTER_ROOM
                # Open door
                # If already opened, no change
                return OpenDoorAction.__name__, {'object_id': self._door['obj_id']}

    def _sendMessage(self, mssg, sender):
        '''
        Enable sending messages in one line of code
        '''
        msg = Message(content=mssg, from_id=sender)
        if msg.content not in self.received_messages:
            self.send_message(msg)

    def _processMessages(self, teamMembers):
        '''
        Process incoming messages and create a dictionary with received messages from each team member.
        '''
        receivedMessages = {}
        for member in teamMembers:
            receivedMessages[member] = []
        for mssg in self.received_messages:
            for member in teamMembers:
                if mssg.from_id == member:
                    receivedMessages[member].append(mssg.content)
        return receivedMessages

    def _parseMessage(self, msg):
        if "Found" in msg.content:
            message = msg.content.split()
            message = message[message.index("Found")+1:]
            print(message)

    def _trustBlief(self, member, received):
        '''
        Baseline implementation of a trust belief. Creates a dictionary with trust belief scores for each team member, for example based on the received messages.
        '''
        # You can change the default value to your preference
        default = 0.5
        trustBeliefs = {}
        for member in received.keys():
            trustBeliefs[member] = default
        for member in received.keys():
            for message in received[member]:
                if 'Found' in message and 'colour' not in message:
                    trustBeliefs[member] -= 0.1
                    break
        return trustBeliefs

    def _traverseRoom(self, min_xy, max_xy):
        self._navigator.reset_full()

        list_coordinates = []
        for x in range(min_xy[0] + 1, max_xy[0]):
            for y in range(min_xy[1] + 1, max_xy[1] - 1):
                list_coordinates.append((x, y))
                # print(x, y)

        self._navigator.add_waypoints(list_coordinates)
