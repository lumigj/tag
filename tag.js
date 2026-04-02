// @flow
function clamp(value: number, low: number, high: number): number {
    if (value < low) {
        return low
    }
    if (value > high) {
        return high
    }
    return value
}
function updateBeaconRssi(beaconId: number, rssi: number) {
    if (beaconId == 1) {
        if (lastSeen1 == 0) {
            smoothedRssi1 = rssi
        } else {
            smoothedRssi1 = Math.round((smoothedRssi1 * 3 + rssi) / 4)
        }
        lastSeen1 = input.runningTime()
        return
    }
    if (beaconId == 2) {
        if (lastSeen2 == 0) {
            smoothedRssi2 = rssi
        } else {
            smoothedRssi2 = Math.round((smoothedRssi2 * 3 + rssi) / 4)
        }
        lastSeen2 = input.runningTime()
    }
}
function hasFreshFix(): boolean {
    now = input.runningTime()
    return lastSeen1 > 0 && lastSeen2 > 0 && now - lastSeen1 <= BEACON_TIMEOUT_MS && now - lastSeen2 <= BEACON_TIMEOUT_MS
}
function rssiToScore(rssi: number): number {
    return clamp(rssi - RSSI_MIN + 1, 1, RSSI_MAX - RSSI_MIN + 1)
}
radio.onReceivedNumber(function (receivedNumber) {
    if (receivedNumber != 1 && receivedNumber != 2) {
        return
    }
    updateBeaconRssi(receivedNumber, radio.receivedPacket(RadioPacketProperty.SignalStrength))
})
let signalConfidence = 0
let score2 = 0
let score1 = 0
let positionPercent = 0
let smoothedRssi2 = 0
let smoothedRssi1 = 0
let now = 0
let lastSeen2 = 0
let lastSeen1 = 0
let RSSI_MAX = 0
let RSSI_MIN = 0
let SEND_INTERVAL_MS = 0
let BEACON_TIMEOUT_MS = 0
let GROUP = 0
GROUP = 23
BEACON_TIMEOUT_MS = 700
SEND_INTERVAL_MS = 180
RSSI_MIN = -95
RSSI_MAX = -40
lastSeen1 = 0
lastSeen2 = 0
smoothedRssi1 = RSSI_MIN
smoothedRssi2 = RSSI_MIN
positionPercent = 50
radio.setGroup(GROUP)
radio.setTransmitPower(7)
basic.showIcon(IconNames.SmallDiamond)
basic.forever(function () {
    if (hasFreshFix()) {
        score1 = rssiToScore(smoothedRssi1)
        score2 = rssiToScore(smoothedRssi2)
        positionPercent = Math.round(score2 * 100 / (score1 + score2))
        signalConfidence = Math.round(((score1 + score2) * 100) / ((RSSI_MAX - RSSI_MIN + 1) * 2))
        signalConfidence = clamp(signalConfidence, 0, 100)
        radio.sendString("T|" + positionPercent + "|" + smoothedRssi1 + "|" + smoothedRssi2 + "|" + score1 + "|" + score2 + "|" + signalConfidence)
        led.plotBarGraph(positionPercent, 100)
    } else {
        basic.clearScreen()
        led.plot(0, 4)
    }
    basic.pause(SEND_INTERVAL_MS)
})
