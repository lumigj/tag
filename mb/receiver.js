// @flow
basic.showIcon(IconNames.Yes)
serial.redirectToUSB()
serial.setBaudRate(BaudRate.BaudRate9600)
radio.onReceivedString(function (receivedString) {
    if (receivedString.charAt(0) == "R") {
        return
    }
    led.toggle(4, 0)
    serial.writeLine(receivedString)
})
radio.setGroup(23)
radio.setFrequencyBand(11)
