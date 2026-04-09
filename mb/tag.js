// @flow
radio.onReceivedNumber(function (receivedNumber) {
    if (receivedNumber != 1 && receivedNumber != 2 && receivedNumber != 3) {
        return
    }
    updateBeaconRssi(receivedNumber, radio.receivedPacket(RadioPacketProperty.SignalStrength))
})
function updateBeaconRssi (beaconId: number, rssi: number) {
    if (beaconId == 1) {
        rawRssi1 = rssi
        lastSeen1 = input.runningTime()
        return
    }
    if (beaconId == 2) {
        rawRssi2 = rssi
        lastSeen2 = input.runningTime()
        return
    }
    rawRssi3 = rssi
    lastSeen3 = input.runningTime()
}
function hasFreshFix () {
    now = input.runningTime()
    if (trackingMode == TRIANGLE_MODE) {
        return lastSeen1 > 0 && lastSeen2 > 0 && lastSeen3 > 0 && now - lastSeen1 <= BEACON_TIMEOUT_MS && now - lastSeen2 <= BEACON_TIMEOUT_MS && now - lastSeen3 <= BEACON_TIMEOUT_MS
    }
    if (trackingMode != LINE_MODE) {
        return false
    }
    return lastSeen1 > 0 && lastSeen2 > 0 && now - lastSeen1 <= BEACON_TIMEOUT_MS && now - lastSeen2 <= BEACON_TIMEOUT_MS
}
function drawModeIndicator () {
    basic.clearScreen()
    if (trackingMode == MOTION_MODE) {
        led.plot(0, 4)
        led.plot(0, 3)
        led.plot(1, 2)
        led.plot(2, 3)
        led.plot(3, 2)
        led.plot(4, 3)
        led.plot(4, 4)
        return
    }
    if (trackingMode == TRIANGLE_MODE) {
        led.plot(1, 0)
        led.plot(2, 0)
        led.plot(3, 0)
        led.plot(3, 1)
        led.plot(2, 2)
        led.plot(3, 2)
        led.plot(3, 3)
        led.plot(1, 4)
        led.plot(2, 4)
        led.plot(3, 4)
    } else {
        led.plot(1, 0)
        led.plot(2, 0)
        led.plot(3, 0)
        led.plot(3, 1)
        led.plot(2, 2)
        led.plot(1, 3)
        led.plot(1, 4)
        led.plot(2, 4)
        led.plot(3, 4)
    }
}
input.onButtonPressed(Button.A, function () {
    trackingMode = LINE_MODE
    modeDisplayUntil = input.runningTime() + MODE_DISPLAY_MS
})
input.onButtonPressed(Button.AB, function () {
    trackingMode = MOTION_MODE
    modeDisplayUntil = input.runningTime() + MODE_DISPLAY_MS
})
input.onButtonPressed(Button.B, function () {
    trackingMode = TRIANGLE_MODE
    modeDisplayUntil = input.runningTime() + MODE_DISPLAY_MS
})
let now = 0
let lastSeen3 = 0
let lastSeen2 = 0
let lastSeen1 = 0
let modeDisplayUntil = 0
let trackingMode = 0
let rawRssi3 = 0
let rawRssi2 = 0
let rawRssi1 = 0
let BEACON_TIMEOUT_MS = 0
let MODE_DISPLAY_MS = 0
let MOTION_SEND_INTERVAL_MS = 0
let LOCATOR_SEND_INTERVAL_MS = 0
let GROUP = 23
let TRIANGLE_MODE = 0
let LINE_MODE = 0
let MOTION_MODE = 0
MOTION_MODE = 1
LINE_MODE = 2
TRIANGLE_MODE = 3
MODE_DISPLAY_MS = 900
BEACON_TIMEOUT_MS = 700
MOTION_SEND_INTERVAL_MS = 100
LOCATOR_SEND_INTERVAL_MS = 180
rawRssi1 = -95
rawRssi2 = -95
rawRssi3 = -95
trackingMode = LINE_MODE
radio.setGroup(GROUP)
radio.setFrequencyBand(11)
radio.setTransmitPower(7)
modeDisplayUntil = input.runningTime() + MODE_DISPLAY_MS
basic.forever(function () {
    if (trackingMode == MOTION_MODE) {
        radio.sendString("A " + input.runningTime() + " " + input.acceleration(Dimension.Strength))
    } else if (hasFreshFix()) {
        if (trackingMode == TRIANGLE_MODE) {
            radio.sendString("T|" + rawRssi1 + "|" + rawRssi2 + "|" + rawRssi3)
        } else {
            radio.sendString("L|" + rawRssi1 + "|" + rawRssi2)
        }
    }
    if (input.runningTime() < modeDisplayUntil) {
        drawModeIndicator()
    } else if (trackingMode == MOTION_MODE) {
        basic.clearScreen()
        led.plot(1, 2)
        led.plot(2, 1)
        led.plot(2, 2)
        led.plot(2, 3)
        led.plot(3, 2)
    } else if (hasFreshFix()) {
        basic.clearScreen()
        if (trackingMode == TRIANGLE_MODE) {
            led.plot(2, 0)
            led.plot(0, 4)
            led.plot(4, 4)
        } else {
            led.plot(1, 2)
            led.plot(3, 2)
        }
    } else {
        basic.clearScreen()
        led.plot(0, 4)
    }
    if (trackingMode == MOTION_MODE) {
        basic.pause(MOTION_SEND_INTERVAL_MS)
    } else {
        basic.pause(LOCATOR_SEND_INTERVAL_MS)
    }
})
