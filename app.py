import network
import socket
import machine
import json
import uasyncio as asyncio
from machine import Pin, ADC
import time

# --- CONFIGURATION ---
WIFI_SSID = 'Pico-Behavior-Lab'
WIFI_PASS = 'science123'
temp_sensor = ADC(4)

def read_temp():
    try:
        raw_val = temp_sensor.read_u16()
        voltage = raw_val * (3.3 / 65535)
        temp_c = 27 - (voltage - 0.706) / 0.001721
        return round(temp_c, 1)
    except:
        return 0.0

# --- HARDWARE MAPPING ---
cage_pins = {
    1: {'out': 4,  'in': list(range(0, 4))},
    2: {'out': 9,  'in': list(range(5, 9))},
    3: {'out': 14, 'in': list(range(10, 14))},
    4: {'out': 19, 'in': list(range(15, 19))}
}

cages = {}
counts = {1: [0]*4, 2: [0]*4, 3: [0]*4, 4: [0]*4}
edges = {1: ['rising']*4, 2: ['rising']*4, 3: ['rising']*4, 4: ['rising']*4}
latched_states = {1: [0]*4, 2: [0]*4, 3: [0]*4, 4: [0]*4}

# --- RECORDING & EVENT LOGIC ---
is_recording = False
is_armed = False
rec_start_time = 0
event_buffer = []

def log_event(cage, signal, state):
    global event_buffer
    # ALWAYS log events (for the 24/7 live plot preview)
    # The browser will decide whether to save it to CSV based on rec_start_time
    ts = time.ticks_ms()
    if len(event_buffer) < 2000:
        event_buffer.append((cage, signal, state, ts))

def trigger_handler(pin):
    global is_recording, is_armed, rec_start_time
    if is_armed:
        is_armed = False
        is_recording = True
        rec_start_time = time.ticks_ms()
        event_buffer.clear()
        for c in counts:
            counts[c] = [0]*4
        print("⚡ External Trigger Received! Recording started.")

trigger_pin = Pin(20, Pin.IN, Pin.PULL_DOWN)
trigger_pin.irq(trigger=Pin.IRQ_RISING, handler=trigger_handler, hard=False)

def irq_handler(pin):
    val = pin.value()
    for c_num, data in cages.items():
        for i, p in enumerate(data['in']):
            if pin == p:
                if val == 1:
                    latched_states[c_num][i] = 1
                
                if edges[c_num][i] == 'rising' and val == 1:
                    counts[c_num][i] += 1
                elif edges[c_num][i] == 'falling' and val == 0:
                    counts[c_num][i] += 1
                
                log_event(c_num, f"DIO_{i+1}", val)
                return

for c, pins in cage_pins.items():
    out_pin = Pin(pins['out'], Pin.OUT)
    out_pin.value(0)
    in_pins = []
    for i, p_num in enumerate(pins['in']):
        p = Pin(p_num, Pin.IN, Pin.PULL_DOWN)
        p.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=irq_handler, hard=False)
        in_pins.append(p)
    cages[c] = {'out': out_pin, 'in': in_pins}

ap = network.WLAN(network.AP_IF)
ap.config(essid=WIFI_SSID, password=WIFI_PASS)
ap.active(True)
while not ap.active():
    pass
print('AP Mode Active. IP:', ap.ifconfig()[0])

async def do_custom_pulse(cage_num, pin, width, freq, p_num, t_num, iti):
    if freq > 0:
        period_ms = 1000 / freq
        off_time_ms = max(0, int(period_ms - width))
    else:
        off_time_ms = 0

    for t in range(t_num):
        for p in range(p_num):
            pin.value(1)
            log_event(cage_num, "Output", 1)
            await asyncio.sleep_ms(width)
            
            pin.value(0)
            log_event(cage_num, "Output", 0)
            
            if p < p_num - 1:
                await asyncio.sleep_ms(off_time_ms)
        if t < t_num - 1:
            await asyncio.sleep_ms(iti)

async def serve_client(reader, writer):
    global is_recording, is_armed, rec_start_time, event_buffer
    try:
        request_line = await reader.readline()
        if not request_line:
            return
        
        request = request_line.decode('utf-8').strip()
        method, path, _ = request.split(' ', 2)
        while await reader.readline() != b'\r\n':
            pass

        if path == '/':
            writer.write('HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n')
            with open('index.html', 'r') as f:
                while True:
                    chunk = f.read(1024)
                    if not chunk: break
                    writer.write(chunk)
                    await writer.drain()
        
        elif path == '/api/data':
            states_to_send = {}
            for c_num in cages:
                states_to_send[c_num] = []
                for i in range(4):
                    current_physical_state = cages[c_num]['in'][i].value()
                    state = 1 if (current_physical_state == 1 or latched_states[c_num][i] == 1) else 0
                    states_to_send[c_num].append(state)
                    latched_states[c_num][i] = 0
            
            response = json.dumps({
                'counts': counts, 
                'states': states_to_send, 
                'temp': read_temp(),
                'is_recording': is_recording,
                'is_armed': is_armed,
                'sys_time': time.ticks_ms(),     # NEW: Send absolute system time for the plot
                'rec_start': rec_start_time,     # NEW: Send recording start time for the CSV
                'events': event_buffer
            })
            event_buffer.clear()
            writer.write('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + response)
        
        elif path.startswith('/api/control'):
            query = path.split('?')[-1]
            params = dict(p.split('=') for p in query.split('&'))
            c_list = list(range(1, 5)) if params.get('cage') == 'all' else [int(params['cage'])]
            action = params['action']
            for c in c_list:
                if action == 'on':
                    cages[c]['out'].value(1)
                    log_event(c, "Output", 1)
                elif action == 'off':
                    cages[c]['out'].value(0)
                    log_event(c, "Output", 0)
                elif action == 'pulse':
                    w = int(params.get('w', 50))
                    f = float(params.get('f', 10))
                    p = int(params.get('p', 1))
                    t = int(params.get('t', 1))
                    i = int(params.get('i', 1000))
                    asyncio.create_task(do_custom_pulse(c, cages[c]['out'], w, f, p, t, i))
            writer.write('HTTP/1.1 200 OK\r\n\r\n{"status":"ok"}')

        elif path.startswith('/api/record'):
            query = path.split('?')[-1]
            params = dict(p.split('=') for p in query.split('&'))
            if params['action'] == 'start':
                if params.get('trigger') == 'true':
                    is_armed = True
                    is_recording = False
                else:
                    is_armed = False
                    is_recording = True
                    rec_start_time = time.ticks_ms()
                event_buffer.clear()
            elif params['action'] == 'stop':
                is_recording = False
                is_armed = False
            writer.write('HTTP/1.1 200 OK\r\n\r\n{"status":"ok"}')

        elif path.startswith('/api/edge'):
            query = path.split('?')[-1]
            params = dict(p.split('=') for p in query.split('&'))
            c = int(params['cage'])
            i = int(params['in'])
            edges[c][i] = params['edge'] 
            writer.write('HTTP/1.1 200 OK\r\n\r\n{"status":"ok"}')

        elif path == '/api/clear':
            for c in counts:
                counts[c] = [0]*4
            writer.write('HTTP/1.1 200 OK\r\n\r\n{"status":"ok"}')
        else:
            writer.write('HTTP/1.1 404 Not Found\r\n\r\n')
        await writer.drain()
    except Exception as e:
        print("Error:", e)
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    server = await asyncio.start_server(serve_client, '0.0.0.0', 80)
    while True:
        await asyncio.sleep(1)

try:
    asyncio.run(main())
except KeyboardInterrupt:
    machine.reset()
