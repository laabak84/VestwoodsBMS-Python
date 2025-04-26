# VestwoodsBMS-Python
A python script for connecting to Vestwoods 100AH BMS through Bluetooth, read the values, and publish them to MQTT in HomeAssistant.

Creds to https://github.com/justinschoeman for his work on the reverse engineering.
Creds to Open AI for building the code based on the work of @justinschoeman

# How to use
Simply run the BMS.py.

The first time you run the script, you will get questions about scanning for bluetooth devices, and if you want to use MQTT.

The values you setup will be stored in config.json and be used for the next and subsequent runs.

For my use, I run the script once every hour to get BMS status sent to my HomeAssistant, triggering alert if the battery voltage gets low. 
