# pyright: reportMissingImports=false
import time
import board
from digitalio import DigitalInOut, Direction, Pull  # GPIO module
from threading import Thread
import serial
import traceback

import const
from Motor import Motor
from Actuator import Actuator


class ROVwinch:
    def __init__(self, mode):
        """
        # initialize input medium
        #  - debug mode      : terminal input
        #  - deployment mode : serial comm via ubox
        #  note: board LED will stay constant on while attempting to establish connection
        """
        self.mode = mode
        
        self.LLC_OE = DigitalInOut(board.D4)
        self.LLC_OE.direction = Direction.OUTPUT
        self.LLC_OE.value = 1

        self.winch = Motor(
            FWD0_REV1_pin=const.Motor.Pins.FWD0_REV1_pin,
            ON_OFF_pin=const.Motor.Pins.ON_OFF_pin,
            mot_pot_pin=const.Motor.Pins.mot_pot_pin,
            rotation_pin=const.Motor.Pins.rotation_pin,
            currentLimit=const.Motor.currentLimit
        )

        self.windActuator = Actuator(
            ON_OFF_pin=const.Actuator.Pins.ONOFFPin,
            direction_pin=const.Actuator.Pins.directionPin,
            feedback_pin=const.Actuator.Pins.feedbackPin,
            readSwitchMinPin=const.Actuator.Pins.readSwitchMin,
            readSwitchMaxPin=const.Actuator.Pins.readSwitchMax
        )

        self.overboardSwitch = DigitalInOut(eval('board.D' + str(const.ROVconst.overboardPin)))
        self.overboardSwitch.direction = Direction.INPUT
        self.overboardSwitch.pull = Pull.DOWN
        self.tensionSwitch = DigitalInOut(eval('board.D' + str(const.ROVconst.tensionPin)))
        self.tensionSwitch.direction = Direction.INPUT
        self.tensionSwitch.pull = Pull.DOWN

        Thread(daemon=True, target=self.winch.monitorCurrent).start()
        Thread(daemon=True, target=self.winch.rotationReedSwitchTracking).start()

        self.heartbeat = DigitalInOut(board.D13)
        self.heartbeat.direction = Direction.OUTPUT

        self.commandsToRun = []


        if mode == 'debug':
            print('COMMAND OPTIONS:')
            print('\t- ROF <ARG1> SPD <arg2> :: move motor (-1 = in | 0 = off | 1 = out) and set speed (<float> '
                  '0<-->100)')
            print('\t- LWA :: Level wind adjust ( CD = change dir. | <float> = move <float> inches)')
            print('\t- TDA :: Tether diameter adjust (<float> set tether diameter to <float> inches)')
            print('\t- CLA :: Current limit adjust (<float> sets motor current limit to <float> amps)')
            print('\t- CLEAR :: Clears queue of commands')

        else:
            while True:
                try:
                    self.uart0 = serial.Serial('/dev/ttyAMA0', baudrate=115200, timeout=0.1)
                    self.heartbeat.value = 0
                    break
                except Exception:
                    time.sleep(1)
                    self.heartbeat.value = 1

        print("initialized")
        Thread(daemon=True, target=self.getCommand).start()

    def handleInput(self, commandInput):
        if len(commandInput) == 0:
            self.turnOffWinchSystem()
            return "Empty command, shutting off winch"

        elif commandInput[0] == "ROF":
            ROF = int(commandInput[1])

            SPD = float(commandInput[3])
            self.winch.set(SPD, ROF)

            return "INFO ROF command ran.\r\n"

            # manually adjust level wind position
        elif commandInput[0] == "LWA":
            if self.winch.ON.value == 0:  # make sure motor is OFF!
                if commandInput[1] == "CD":
                    self.windActuator.changeDirection()
                    return "INFO Level wind direction changed.\r\n"
                else:
                    self.windActuator.manualAdjust(commandInput[1], self.winch.direction)
                    return "INFO Level wind adjusted.\r\n"
            else:
                return "INFO Motor must be stationary before adjusting level wind.\r\n"

            # Adjust cable diameter parameter
        elif commandInput[0] == "TDA":
            if self.winch.ON.value == 0:  # make sure motor is OFF!
                self.windActuator.cable_diameter = float(commandInput[1])
                return "INFO Cable diameter updated.\r\n"
            else:
                return "INFO Motor must be stationary before changing parameter.\r\n"

            # Adjust current limit
        elif commandInput[0] == "CLA":
            self.winch.current_limit = float(commandInput[1])
            return "INFO Current limit updated.\r\n"

        else:
            return "INFO invalid input.\r\n"

    def getCommand(self):
        while True:
            try:
                if self.mode != 'debug':
                    serial_in = self.uart0.readline()
                    in_decoded = serial_in.decode('UTF-8')
                    in_strings = in_decoded.split()
                    if in_strings:
                        self.commandsToRun.append(in_strings)
                else:
                    in_strings = input('Input (<COMMAND> <VALUE>) : ')

                    if in_strings == "CLEAR":
                        print("Queue Cleared")
                        self.commandsToRun.clear()
                    else:
                        self.commandsToRun.append(in_strings.split())

            except Exception:
                print("Error input")
                self.commandsToRun.clear()

            time.sleep(const.ROVconst.getCommandSleep)

    def turnOffWinchSystem(self):
        self.winch.off()
        self.windActuator.setSpeed(0)

    def control_winch(self):
        while True:

            if self.overboardSwitch.value and self.winch.getDirection() == const.Motor.TAKE:
                print("Pulled in!")
                self.turnOffWinchSystem()

            if self.tensionSwitch.value and self.winch.getDirection() == const.Motor.FEED:
                print("Line tensioning limit reached")
                self.turnOffWinchSystem()

            # END TODO

            if self.winch.NeedToMoveActuator:
                Thread(daemon=True, target=self.windActuator.moveCableDistance, args=(self.winch.direction,)).start()
                self.winch.NeedToMoveActuator = False

            if len(self.commandsToRun) > 0:
                try:
                    out_string = self.handleInput(self.commandsToRun[0])
                    if self.mode == 'debug':
                        if out_string != "INFO invalid input.\r\n":
                            print(out_string)
                    else:
                        if out_string.split()[0] == "INFO":
                            print(out_string)

                except Exception:
                    out_string = "INFO Exception raised. Turning off winch."
                    print(out_string)
                    self.turnOffWinchSystem()
                    print(traceback.format_exc())

                self.commandsToRun.pop(0)

            else:
                out_string = "ROT " + str(self.winch.rotationCounter) + "\r\n"

            if self.mode != 'debug':
                self.uart0.write(bytes(out_string, 'utf-8'))

            self.heartbeat.value = not self.heartbeat.value  # toggle LED
            time.sleep(const.ROVconst.controlSleep)
