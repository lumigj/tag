# python -m pip install paho-mqtt

import sqlite3
import random
import time
import json
import paho.mqtt.client as mqtt

DB_N = "hub_u1.db"



def on_connect(client, userdata, flags, rc):
	
	if rc == 0:
	
		print('Connected to MQTT Broker!')
		
	else:
	
		print('Failed to connect, return code {:d}'.format(rc))

def relay_unsent_data(client,topic):
	conn = sqlite3.connect(DB_N)
	c = conn.cursor()
	c.execute("""SELECT id, timestamp, running_time, strength, session_id 
				FROM accelerometer WHERE uploaded = 0
				ORDER BY id ASC
			""")
	results = c.fetchall()
	c = conn.cursor()

	for result in results:
		row_id, timestamp, running_time, strength, session_id = result

		print(
			"Relaying id={}; timestamp={}; running_time={}; strength={}; session_id={}".format(
				row_id, timestamp, running_time, strength, session_id
			)
		)

		payload = {
			"timestamp": timestamp,
			"running_time": running_time,
			"strength": strength,
			"session_id": session_id
		}
		try:
			msg_info = client.publish(topic, json.dumps(payload))
			msg_info.wait_for_publish(timeout=1.0) 

			if msg_info.rc == mqtt.MQTT_ERR_SUCCESS:
				c.execute(
					"UPDATE accelerometer SET uploaded = 1 WHERE id = ?",
					(row_id,)
				)
				print("Marked id={} as uploaded.".format(row_id))
			else:
				print("Failed to publish id={}.".format(row_id))
		except Exception as e:
			print(f"Publish error for id={row_id}: {e}")

	conn.commit()
	conn.close()



def run():

	try:
	
		broker = 'broker.emqx.io'
		port = 1883
		topic = '/is4151-group04/usecase1'
		client_id = 'rpi_u1_sender'
		username = 'emqx'
		password = 'public'

		print('client_id={}'.format(client_id))



		# Set Connecting Client ID
		client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
		client.username_pw_set(username, password)
		client.on_connect = on_connect
		client.connect(broker, port)

		client.loop_start()
		
		while True:
			try:
				relay_unsent_data(client,topic)
			except Exception as e:
				print("Error during relay:", e)	
			time.sleep(1)

	except KeyboardInterrupt:

		print('Program terminated!')
	
	except Exception as e:

		print('Error occurred: {}'.format(e))



if __name__ == '__main__':
	
	run()