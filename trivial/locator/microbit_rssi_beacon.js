// @flow
let GROUP = 0
GROUP = 23

radio.setGroup(GROUP)
radio.setTransmitPower(1)

basic.showIcon(IconNames.SmallDiamond)

basic.forever(function () {
    radio.sendNumber(1)
    basic.pause(150)
})
