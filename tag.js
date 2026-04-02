// @flow
input.onButtonPressed(Button.A, function () {
    trackingMode = 2
    showMode()
})
input.onButtonPressed(Button.AB, function () {
    showMode()
})
input.onButtonPressed(Button.B, function () {
    trackingMode = 3
    showMode()
})
radio.onReceivedNumber(function (receivedNumber) {
    if (receivedNumber != 1 && receivedNumber != 2 && receivedNumber != 3) {
        return
    }
    updateBeaconRssi(receivedNumber, radio.receivedPacket(RadioPacketProperty.SignalStrength))
})
function updateBeaconRssi(beaconId: number, rssi: number) {
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
function hasFreshFix() {
    now = input.runningTime()
    if (trackingMode == 3) {
        return lastSeen1 > 0 && lastSeen2 > 0 && lastSeen3 > 0 && now - lastSeen1 <= BEACON_TIMEOUT_MS && now - lastSeen2 <= BEACON_TIMEOUT_MS && now - lastSeen3 <= BEACON_TIMEOUT_MS
    }
    return lastSeen1 > 0 && lastSeen2 > 0 && now - lastSeen1 <= BEACON_TIMEOUT_MS && now - lastSeen2 <= BEACON_TIMEOUT_MS
}
function showMode() {
    basic.clearScreen()
    if (trackingMode == 3) {
        basic.showString("123")
    } else {
        basic.showString("12")
    }
}
let trackingMode = 0
let now = 0
let rawRssi3 = 0
let rawRssi2 = 0
let rawRssi1 = 0
let lastSeen3 = 0
let lastSeen2 = 0
let lastSeen1 = 0
let BEACON_TIMEOUT_MS = 0
let GROUP = 23
BEACON_TIMEOUT_MS = 700
let SEND_INTERVAL_MS = 180
rawRssi1 = -95
rawRssi2 = -95
rawRssi3 = -95
trackingMode = 2
radio.setGroup(GROUP)
radio.setFrequencyBand(11)
radio.setTransmitPower(7)
showMode()
basic.forever(function () {
    if (hasFreshFix()) {
        basic.clearScreen()
        if (trackingMode == 3) {
            led.plot(2, 0)
            led.plot(0, 4)
            led.plot(4, 4)
            radio.sendString("T|3|" + rawRssi1 + "|" + rawRssi2 + "|" + rawRssi3)
        } else {
            led.plot(1, 2)
            led.plot(3, 2)
            radio.sendString("T|2|" + rawRssi1 + "|" + rawRssi2)
        }
    } else {
        basic.clearScreen()
        led.plot(0, 4)
    }
    basic.pause(SEND_INTERVAL_MS)
})
