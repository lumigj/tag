// @flow
basic.showIcon(IconNames.Yes)
serial.redirectToUSB()
serial.setBaudRate(BaudRate.BaudRate9600)
basic.forever(function () {
    led.toggle(4, 0)
    serial.writeLine("TEST|receiver|" + input.runningTime())
    basic.pause(1000)
})
