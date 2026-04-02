// @flow
input.onButtonPressed(Button.A, function () {
    beaconId = 1
    showBeaconId()
})
function showBeaconId () {
    basic.showString("B")
    basic.showNumber(beaconId)
}
input.onButtonPressed(Button.B, function () {
    beaconId = 2
    showBeaconId()
})
let beaconId = 0
let GROUP = 23
let SEND_DELAY_MS_1 = 170
let SEND_DELAY_MS_2 = 230
beaconId = 1
radio.setGroup(GROUP)
radio.setFrequencyBand(11)
radio.setTransmitPower(7)
showBeaconId()
basic.forever(function () {
    radio.sendNumber(beaconId)
    if (beaconId == 1) {
        basic.pause(SEND_DELAY_MS_1)
    } else {
        basic.pause(SEND_DELAY_MS_2)
    }
})
