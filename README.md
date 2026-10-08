# OTA-MTQQ-Merkle
This guide sets up a workstation as the MQTT broker and a Raspberry Pi as a client.
## 1. Workstation (broker)
first set up the workstation to act as a broker
### Install
```bash
sudo apt update
sudo apt install mosquitto mosquitto-clients
```
### Configure
We had to edit the config to make things work:
```bash
sudo nano /etc/mosquitto/mosquitto.conf
```
add the following lines to the mosquitto.conf file
```
listener 1883
allow_anonymous true
```

### Start
run the following commands to start the mosquitto service

```bash
sudo systemctl enable --now mosquitto
sudo systemctl status mosquitto --no-pager
```

### Open the firewall
we had to change firewall rules to make it work

```bash
# ufw
sudo ufw allow 1883/tcp
```
## 2. Raspberry Pi (client)
The Pi only needs the client tools, not the broker.

```bash
sudo apt update
sudo apt install mosquitto-clients
```

If the broker package is installed and failing, disable it:

```bash
sudo systemctl disable --now mosquitto
```
## 3. Running the ota-client code (run this first)
To run the ota_clinet.py code run the following command
```bash
python3 ota_client.py --broker {broker_ip}
```
replace {broker_ip} with the ip of the MQTT broker (should be workstation).
## 4.Running the ota-server code 
now run the server code to publish a firmware file.

```bash
python3 run.py {firmware_file}.txt --version {version_num}
```




