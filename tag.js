// @flow
radio.onReceivedNumber(function (receivedNumber) {
    if (receivedNumber != 1 && receivedNumber != 2) {
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
    }
}
function hasFreshFix() {
    now = input.runningTime()
    return lastSeen1 > 0 && lastSeen2 > 0 && now - lastSeen1 <= BEACON_TIMEOUT_MS && now - lastSeen2 <= BEACON_TIMEOUT_MS
}
let now = 0
let rawRssi2 = 0
let rawRssi1 = 0
let lastSeen2 = 0
let lastSeen1 = 0
let BEACON_TIMEOUT_MS = 0
let GROUP = 23
BEACON_TIMEOUT_MS = 700
let SEND_INTERVAL_MS = 180
rawRssi1 = -95
rawRssi2 = -95
radio.setGroup(GROUP)
radio.setFrequencyBand(11)
radio.setTransmitPower(7)
basic.showIcon(IconNames.SmallDiamond)
basic.forever(function () {
    if (hasFreshFix()) {
        basic.clearScreen()
        led.plot(2, 2)
        radio.sendString("T|" + rawRssi1 + "|" + rawRssi2)
    } else {
        basic.clearScreen()
        led.plot(0, 4)
    }
    basic.pause(SEND_INTERVAL_MS)
})
