"""
This file contains 2 classes:
    - ParseUtils containing utilies to parse data from UR robot
    - SecondaryMonitor, a class opening a socket to the robot and with methods to access data and send programs to the robot
Both use data from the secondary port of the URRobot.
Only the last connected socket on 3001 is the primary client !!!! so it is unreliable to rely on it
http://support.universal-robots.com/Technical/PrimaryAndSecondaryClientInterface
"""

__author__ = "Olivier Roulet-Dubonnet"
__copyright__ = "Copyright 2011-2012, Olivier Roulet-Dubonnet"
__credits__ = ["Olivier Roulet-Dubonnet"]
__license__ = "GPLv3"
__version__ = "0.4"
__status__ = "Development"

from threading import Thread, Condition, Lock
import logging
import struct 
import socket
from copy import copy



class ParsingException(Exception):
    def __init__(self, *args):
        Exception.__init__(self, *args)



class ParserUtils(object):
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def parse(self, data):
        """
        parse a packet from the UR socket and return a dictionary with the data
        """
        allData = {}
        #print "Total size ", len(data)
        while data:
            psize, ptype, pdata, data = self.analyze_header(data)
            #print "We got packet with size %i and type %s" % (psize, ptype)
            if ptype == 16:
                allData["SecondaryClientData"] = self._get_data(pdata, "!iB", ("size", "type"))
                data = (pdata + data)[5:] # This is the total size so we resend data to parser
            elif ptype == 0:
                allData["RobotModeData"] = self._get_data(pdata, "!iBQ???????Bd", ("size", "type", "timestamp", "isRobotConnected", "isRealRobotEnabled", "isPowerOnRobot", "isEmergencyStopped", "isSecurityStopped", "isProgramRunning", "isProgramPaused", "robotMode", "speedFraction"))
            elif ptype == 1:
                tmpstr = ["size", "type"]
                for i in range(0, 6):
                    tmpstr += ["q_actual%s" % i, "q_target%s" % i, "qd_actual%s" % i, "I_actual%s" % i, "V_actual%s" % i, "T_motor%s" % i ,"T_micro%s" % i, "jointMode%s" % i]

                allData["JointData"] = self._get_data(pdata, "!iB dddffffB dddffffB dddffffB dddffffB dddffffB dddffffB", tmpstr)

            elif ptype == 4:
                allData["CartesianInfo"] = self._get_data(pdata, "iBdddddd", ("size", "type", "X", "Y", "Z", "Rx", "Ry", "Rz"))
            elif ptype == 5:
                allData["LaserPointer(OBSOLETE)"] = self._get_data(pdata, "iBddd" , ("size", "type"))
            elif ptype == 3:
                allData["MasterBoardData"] = self._get_data(pdata, "iBhhbbddbbddffffBBb" , ("size", "type", "digitalInputBits", "digitalOutputBits", "analogInputRange0", "analogInputRange1", "analogInput0", "analogInput1", "analogInputDomain0", "analogInputDomain1", "analogOutput0", "analogOutput1", "masterBoardTemperature", "robotVoltage48V", "robotCurrent", "masterIOCurrent"))#, "masterSafetyState" ,"masterOnOffState", "euromap67InterfaceInstalled"   ))
            elif ptype == 2:
                allData["ToolData"] = self._get_data(pdata, "iBbbddfBffB" , ("size", "type", "analoginputRange2", "analoginputRange3", "analogInput2", "analogInput3", "toolVoltage48V", "toolOutputVoltage", "toolCurrent", "toolTemperature", "toolMode" ))

            elif ptype == 20:
                tmp = self._get_data(pdata, "!iB Qbb", ("size", "type", "timestamp", "source", "robotMessageType"))
                if tmp["robotMessageType"] == 3:
                    allData["VersionMessage"] = self._get_data(pdata, "!iBQbb bAbBBiAb", ("size", "type", "timestamp", "source", "robotMessageType", "projectNameSize", "projectName", "majorVersion", "minorVersion", "svnRevision", "buildDate"))
                elif tmp["robotMessageType"] == 6:
                    allData["robotCommMessage"] = self._get_data(pdata, "!iBQbb iiAc", ("size", "type", "timestamp", "source", "robotMessageType", "code", "argument", "messageText"))
                elif tmp["robotMessageType"] == 1:
                    allData["labelMessage"] = self._get_data(pdata, "!iBQbb iAc", ("size", "type", "timestamp", "source", "robotMessageType", "id", "messageText"))
                elif tmp["robotMessageType"] == 2:
                    allData["popupMessage"] = self._get_data(pdata, "!iBQbb ??BAcAc", ("size", "type", "timestamp", "source", "robotMessageType", "warning", "error", "titleSize", "messageTitle", "messageText"))
                elif tmp["robotMessageType"] == 0:
                    allData["messageText"] = self._get_data(pdata, "!iBQbb Ac", ("size", "type", "timestamp", "source", "robotMessageType", "messageText"))
                elif tmp["robotMessageType"] == 8:
                    allData["varMessage"] = self._get_data(pdata, "!iBQbb iiBAcAc", ("size", "type", "timestamp", "source", "robotMessageType", "code", "argument", "titleSize", "messageTitle", "messageText"))
                elif tmp["robotMessageType"] == 7:
                    allData["keyMessage"] = self._get_data(pdata, "!iBQbb iiBAcAc", ("size", "type", "timestamp", "source", "robotMessageType", "code", "argument", "titleSize", "messageTitle", "messageText"))
                elif tmp["robotMessageType"] == 5:
                    allData["keyMessage"] = self._get_data(pdata, "!iBQbb iiAc", ("size", "type", "timestamp", "source", "robotMessageType", "code", "argument", "messageText"))
                else:
                    print("Message type parser not implemented ", tmp)
            else:
                print("Uknown packet type %s with size %s" % (ptype, psize))

        return allData

    def _get_data(self, data, fmt, names):
        """
        fill data into a dictionary
            data is data from robot packet
            fmt is struct format, but with added A for arrays and no support for numerical in fmt
            names args are strings used to store values
        """
        tmpdata = copy(data)
        fmt = fmt.strip() # space may confuse us
        d = dict()
        i = 0
        j = 0
        while j < len(fmt) and i < len(names):
            f = fmt[j]
            if f in (" ", "!", ">", "<"):
                j += 1
            elif f == "A": #we got an array 
                # first we need to find its size
                if j == len(fmt) - 2: # we are last element, size is the rest of data in packet
                    arraysize = len(tmpdata)
                else: # size should be given in last element
                    asn =  names[i-1]
                    if not asn.endswith("Size"):
                        raise ParsingException("Error, array without size ! %s %s" % (asn, i))
                    else:
                        arraysize = d[asn]
                d[names[i]] = tmpdata[0:arraysize]
                #print "Array is ", names[i], d[names[i]]
                tmpdata = tmpdata[arraysize:]
                j += 2
                i += 1
            else:
                fmtsize = struct.calcsize(fmt[j])
                #print "reading ", f , i, j,  fmtsize, len(tmpdata)
                if len(tmpdata) < fmtsize: #seems to happen on windows
                    raise ParsingException("Error, length of data smaller than advertized: ", len(tmpdata), fmtsize, "for names ", names, f, i, j)
                d[names[i]] = struct.unpack("!" + f, tmpdata[0:fmtsize])[0]
                #print names[i], d[names[i]]
                tmpdata = tmpdata[fmtsize:]
                j += 1
                i += 1
        return d 

    def get_header(self, data):
        return struct.unpack("!iB", data[0:5])

    def analyze_header(self, data):
        """
        read first 5 bytes and return complete packet
        """
        if not len(data) >= 5:
            raise ParsingException("Packet size %s smaller than header size (5 bytes)" % len(data))
        else:
            psize, ptype = self.get_header(data)
            if psize < 5: 
                raise ParsingException("Error, declared length of data smaller than its own header(5): ", psize)
            elif psize > len(data):
                raise ParsingException("Error, length of data smaller (%s) than declared (%s)" %( len(data), psize))
        return psize, ptype, data[:psize], data[psize:]

    def find_first_packet(self, data):
        """
        find the first complete packet in a string
        returns None if none found
        """
        counter = 0
        while True:
            if len(data) >= 5:
                psize, ptype = self.get_header(data)
                if psize < 5 or psize > 500 or ptype != 16:
                    data = data[1:]
                elif len(data) > psize:
                    if counter:
                        self.logger.info("Remove {} bytes of garbage at begining of packet".format(counter))
                    #ok we we have somehting which looks like a packet"
                    return (data[:psize], data[psize:])
                else:
                    #packet is not complete
                    return None
            else:
                return None



class SecondaryMonitor(Thread):
    """
    Monitor data from secondary port and send programs to robot
    """
    def __init__(self, host):
        Thread.__init__(self)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._parser = ParserUtils()
        self._s_secondary = None
        self._dict = {}
        self._dictLock = Lock()
        self.host = host
        secondary_port = 30002    # Secondary client interface on Universal Robots
        self._s_secondary = socket.create_connection((self.host, secondary_port), timeout=0.5)
        self._prog_queue = []
        self._prog_queue_lock = Lock()
        self._dataqueue = bytes()
        self._trystop = False # to stop thread
        self.running = False #True when robot is on and listening
        self._dataEvent = Condition()

        self.start()
        self._dataEvent.wait() # make sure we got some data before someone calls us

    def sendProgram(self, prog):
        """
        send program to robot in URRobot format
        If another program is send while a program is running the first program is aborded. 
        """
        with self._prog_queue_lock:
            prog.strip()
            self.logger.debug("Sending program: prog")
            self._prog_queue.append(prog.encode() + b"\n")
 

    def run(self):
        """
        check program execution status in the secondary client data packet we get from the robot 
        This interface uses only data from the secondary client interface (see UR doc)
        Only the last connected client is the primary client, 
        so this is not guaranted and we cannot rely on information only 
        send to the primary client(like program execution start ?!?!?)
        """

        while not self._trystop:
            with self._prog_queue_lock:
                if len(self._prog_queue) > 0:
                    prog = self._prog_queue.pop(0)
                    self._s_secondary.send(prog)
            
            data = self._get_data()
            try:
                with self._dictLock:
                    self._dict = self._parser.parse(data)
            except ParsingException as ex:
                self.logger.warn("Error parsing data from urrobot: " + str(ex) )
                continue

            if "RobotModeData" not in self._dict:
                self.logger.warn( "Got a packet from robot without RobotModeData, strange ...")
                continue

            if self._dict["RobotModeData"]["robotMode"] == 0 \
                            and self._dict["RobotModeData"]["isRealRobotEnabled"] == True \
                            and self._dict["RobotModeData"]["isEmergencyStopped"] == False \
                            and self._dict["RobotModeData"]["isSecurityStopped"] == False \
                            and self._dict["RobotModeData"]["isRobotConnected"] == True \
                            and self._dict["RobotModeData"]["isPowerOnRobot"] == True:
                self.running = True
            else:
                if self.running == True:
                    self.logger.error("Robot not running: " + str( self._dict["RobotModeData"]))
                self.running = False
            self._dataEvent.notifyAll()

    def _get_data(self):
        """
        returns something that looks like a packet, nothing is guaranted
        """
        while True:
            ans = self._parser.find_first_packet(self._dataqueue[:])
            if ans:
                self._dataqueue = ans[1]
                return ans[0]
            else:
                tmp = self._s_secondary.recv(1024)
                self._dataqueue += tmp

    def getCartesianInfo(self):
        with self._dictLock:
            if "CartesianInfo" in self._dict:
                return self._dict["CartesianInfo"]
            else:
                return None

    def getAllData(self):
        """
        return last data obtained from robot in dictionnary format
        """
        with self._dictLock:
            return self._dict.copy()


    def getJointData(self):
        with self._dictLock:
            if "JointData" in self._dict:
                return self._dict["JointData"]
            else:
                return None

    def isProgramRunning(self):
        """
        return True if robot is executing a program
        Rmq: The refresh rate is only 10Hz so the information may be outdated
        """
        with self._dictLock:
            return self._dict["RobotModeData"]["isProgramRunning"]


    def cleanup(self):
        self._trystop = True
        self.join()
        if self._s_secondary:
            with self._prog_queue_lock:
                self._s_secondary.close()



