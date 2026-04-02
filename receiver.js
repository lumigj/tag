// @flow
radio.onReceivedString(function (receivedString) {
    if (receivedString.charAt(0) != "T") {
        return
    }
    serial.writeLine(receivedString)
})
radio.setGroup(23)
serial.redirectToUSB()
serial.setBaudRate(BaudRate.BaudRate115200)
basic.showIcon(IconNames.Yes)
