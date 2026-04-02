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
radio.onReceivedNumber(function (receivedNumber) {
    let rssi = 0
    let instantIndex = 0
    rssi = clamp(radio.receivedPacket(RadioPacketProperty.SignalStrength), RSSI_FAR, RSSI_NEAR)
    instantIndex = Math.round(Math.map(rssi, RSSI_NEAR, RSSI_FAR, 0, 999))
    if (lastSeen == 0) {
        distanceIndex = instantIndex
    } else {
        distanceIndex = Math.round((distanceIndex * 3 + instantIndex) / 4)
    }
    lastSeen = input.runningTime()
    display.show(distanceIndex)
})
let lastSeen = 0
let distanceIndex = 0
let RSSI_FAR = 0
let RSSI_NEAR = 0
let LOST_TIMEOUT_MS = 0
let GROUP = 0
let display = grove.createDisplay(DigitalPin.P2, DigitalPin.P16)
GROUP = 23
LOST_TIMEOUT_MS = 1200
RSSI_NEAR = -35
RSSI_FAR = -95
distanceIndex = 9999
radio.setGroup(GROUP)
display.set(7)
display.show(distanceIndex)
basic.forever(function () {
    if (lastSeen > 0 && input.runningTime() - lastSeen > LOST_TIMEOUT_MS) {
        distanceIndex = 9999
        display.show(distanceIndex)
        lastSeen = 0
    }
    basic.pause(200)
})
