#!/usr/bin/env python3
"""Local browser-based trip planner for the SKia EV9 Long Range AWD.

Runs on Python's standard library only. Estimates are planning approximations,
not live navigation or vehicle telemetry.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import webbrowser

# Render and similar hosts provide PORT and require binding to 0.0.0.0.
# Locally, keep the app private to this Mac and use port 8765 by default.
PORT = int(os.environ.get("PORT", "8765"))
HOST = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SKia EV9 Trip Planner</title>
<style>
:root{--bg:#f3f5f8;--card:#fff;--ink:#172033;--muted:#667085;--line:#e2e7ef;--blue:#2457d6;--blue2:#eaf0ff;--green:#147d55;--amber:#a45a00;--shadow:0 8px 28px rgba(21,35,65,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{background:#101a2d;color:white;padding:26px max(22px,calc((100vw - 1180px)/2));}header .eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:#aebfe9;font-weight:700}h1{margin:6px 0 4px;font-size:29px;letter-spacing:-.03em}header p{margin:0;color:#c8d1e4;max-width:680px}
main{max-width:1180px;margin:24px auto;padding:0 18px 40px}.layout{display:grid;grid-template-columns:minmax(320px, .95fr) minmax(380px,1.25fr);gap:20px;align-items:start}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:21px;margin-bottom:18px}.card h2{font-size:17px;margin:0 0 16px;letter-spacing:-.01em}.section-label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.11em;font-weight:800;margin:20px 0 10px}.section-label:first-of-type{margin-top:0}.fields{display:grid;grid-template-columns:1fr 1fr;gap:13px}.field.full{grid-column:1/-1}label{display:block;font-size:12px;color:#465066;font-weight:700;margin-bottom:6px}input,select{width:100%;border:1px solid #cfd6e2;border-radius:9px;padding:10px 11px;font:inherit;color:var(--ink);background:#fff;outline:none}input:focus,select:focus{border-color:var(--blue);box-shadow:0 0 0 3px #2457d61a}.unit{font-weight:500;color:var(--muted)}.hint{font-size:11px;color:var(--muted);margin-top:5px}.button{border:0;border-radius:10px;background:var(--blue);color:white;font-size:14px;font-weight:750;padding:12px 16px;width:100%;cursor:pointer;margin-top:18px}.button:hover{background:#1c45ae}.summary{background:linear-gradient(135deg,#142342,#203c72);color:#fff;border-radius:16px;padding:23px;box-shadow:var(--shadow);margin-bottom:18px}.summary .topline{color:#c1d0f4;text-transform:uppercase;font-size:11px;letter-spacing:.12em;font-weight:800}.big{font-size:38px;font-weight:800;letter-spacing:-.04em;margin:4px 0}.summary p{color:#d4def5;margin:0;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:20px}.metric{border:1px solid #ffffff2b;background:#ffffff0d;border-radius:11px;padding:12px}.metric span{display:block;color:#c1d0f4;font-size:11px}.metric strong{display:block;font-size:18px;margin-top:3px}.stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:11px}.stat{padding:14px;border:1px solid var(--line);border-radius:12px}.stat .label{font-size:12px;color:var(--muted)}.stat .value{font-size:23px;font-weight:800;letter-spacing:-.03em;margin-top:3px}.stat .detail{font-size:11px;color:var(--muted);margin-top:2px}.bar{height:8px;background:#e9edf4;border-radius:99px;overflow:hidden;margin-top:12px}.bar div{height:100%;background:var(--blue);width:0%;transition:width .2s}.stops{display:flex;flex-direction:column;gap:10px}.stop{display:grid;grid-template-columns:38px 1fr auto;gap:12px;align-items:center;border:1px solid var(--line);border-radius:12px;padding:13px}.stopnum{width:34px;height:34px;border-radius:10px;background:var(--blue2);color:var(--blue);display:grid;place-items:center;font-weight:800}.stop strong{display:block;font-size:13px}.stop small{display:block;color:var(--muted);font-size:12px;margin-top:3px}.duration{text-align:right;font-weight:800;white-space:nowrap}.duration small{font-weight:500;color:var(--muted)}.notice{font-size:12px;color:#5b4b2e;background:#fff7e8;border:1px solid #f2dfba;border-radius:11px;padding:12px 14px;margin-top:14px}.muted{color:var(--muted)}.footer{font-size:11px;color:var(--muted);margin:14px 3px}.pill{display:inline-block;background:#e7f6ef;color:var(--green);padding:4px 8px;border-radius:99px;font-size:11px;font-weight:800;margin-bottom:10px}
@media(max-width:800px){.layout{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr 1fr}main{margin-top:14px}.card{padding:17px}}@media(max-width:450px){.fields{grid-template-columns:1fr}.field.full{grid-column:auto}.metrics{grid-template-columns:1fr}.stat-grid{grid-template-columns:1fr 1fr}}
</style></head><body>
<header><div class="eyebrow">Personal EV planning tool · Prototype</div><h1>SKia EV9 Trip Planner</h1><p>Estimate real-world range, charging stops and total journey time from your route and driving conditions.</p></header>
<main><div class="layout"><section>
<div class="card"><h2>Trip and driving conditions</h2>
<div class="section-label">Journey</div><div class="fields">
<div class="field"><label for="origin">From <span class="unit">(optional label)</span></label><input id="origin" value="Vught" placeholder="Starting point"></div>
<div class="field"><label for="destination">To <span class="unit">(optional label)</span></label><input id="destination" placeholder="Destination"></div>
<div class="field full"><label for="distance">Route distance <span class="unit">km</span></label><input id="distance" type="number" min="1" max="5000" value="700" step="1"></div>
</div>
<div class="section-label">Driving profile</div><div class="fields">
<div class="field"><label for="speed">Average moving speed <span class="unit">km/h</span></label><input id="speed" type="number" min="40" max="160" value="100"></div>
<div class="field"><label for="temp">Outside temperature <span class="unit">°C</span></label><input id="temp" type="number" min="-25" max="45" value="0"></div>
<div class="field"><label for="terrain">Terrain</label><select id="terrain"><option value="flat">Mostly flat</option><option value="rolling" selected>Rolling / mixed</option><option value="mountain">Mountainous</option></select></div>
<div class="field"><label for="load">Vehicle load</label><select id="load"><option value="light">Driver / light load</option><option value="normal">Normal family load</option><option value="heavy" selected>Full family + luggage</option></select></div>
<div class="field"><label for="climate">Cabin heating / cooling</label><select id="climate"><option value="eco">Eco / preconditioned</option><option value="normal" selected>Normal</option><option value="high">High heating / cooling</option></select></div>
<div class="field"><label for="base">Baseline consumption <span class="unit">kWh/100 km</span></label><input id="base" type="number" min="12" max="35" step="0.5" value="21"></div>
<div class="field full"><label for="ascent">Net elevation gain over the trip <span class="unit">metres</span></label><input id="ascent" type="number" min="-3000" max="6000" step="50" value="0"><div class="hint">Enter the approximate net gain from start to finish. Mountain routes can consume more energy even when the destination is lower, due to climbing and imperfect regeneration.</div></div>
</div>
<div class="section-label">Battery and charging</div><div class="fields">
<div class="field"><label for="capacity">Usable battery <span class="unit">kWh</span></label><input id="capacity" type="number" min="40" max="100" step="0.1" value="96.0"></div>
<div class="field"><label for="peak">Maximum DC charging <span class="unit">kW</span></label><input id="peak" type="number" min="50" max="350" value="235"></div>
<div class="field"><label for="start">Starting charge <span class="unit">%</span></label><input id="start" type="number" min="10" max="100" value="100"></div>
<div class="field"><label for="reserve">Arrival reserve <span class="unit">%</span></label><input id="reserve" type="number" min="5" max="40" value="10"></div>
<div class="field"><label for="target">Charging target <span class="unit">%</span></label><input id="target" type="number" min="50" max="100" value="80"></div>
<div class="field"><label for="overhead">Overhead per stop <span class="unit">minutes</span></label><input id="overhead" type="number" min="0" max="20" value="5"></div>
</div><button class="button" id="calculate">Calculate trip plan</button>
</div></section><section>
<div class="summary"><div class="topline">Estimated total journey time</div><div class="big" id="totalTime">Calculating…</div><p id="routeLabel">700 km · provisional estimate</p><div class="metrics"><div class="metric"><span>Driving time</span><strong id="driveTime">—</strong></div><div class="metric"><span>Charging + stop time</span><strong id="chargeTime">—</strong></div><div class="metric"><span>Charging stops</span><strong id="stopCount">—</strong></div></div></div>
<div class="card"><h2>Real-world energy estimate</h2><div class="stat-grid">
<div class="stat"><div class="label">Estimated consumption</div><div class="value" id="consumption">—</div><div class="detail">kWh per 100 km</div></div>
<div class="stat"><div class="label">Usable range to reserve</div><div class="value" id="range">—</div><div class="detail">km from current charge</div></div>
<div class="stat"><div class="label">Trip energy required</div><div class="value" id="energy">—</div><div class="detail">net traction + conditions estimate</div></div>
<div class="stat"><div class="label">Arrival state of charge</div><div class="value" id="arrival">—</div><div class="detail" id="arrivalDetail">after planned charging</div><div class="bar"><div id="arrivalBar"></div></div></div>
</div><div class="notice" id="assumptions">Estimates are indicative and should be calibrated against your actual consumption.</div></div>
<div class="card"><h2>Suggested charging strategy</h2><div id="stops" class="stops"><p class="muted">Enter trip settings and calculate to see a charging plan.</p></div><p class="footer">Stops are calculated from battery energy and an approximate charging curve. This prototype does not yet identify actual charging stations or road detours.</p></div>
<div class="card"><h2>How to interpret this estimate</h2><p class="muted" style="margin:0;font-size:13px">This first version uses a configurable baseline, speed, temperature, load, terrain and net elevation adjustment. It is not connected to live traffic, route elevation profiles, charger availability, battery preconditioning or your vehicle's telemetry. The EV9 battery capacity is the supplied approximate usable figure; its charge curve is an approximation, not an OEM-verified curve.</p></div>
</section></div><div class="footer">Local app · Data stays in this browser session · No account or external service required</div></main>
<script>
const $=id=>document.getElementById(id); const n=id=>Number($(id).value);
function fmtTime(min){min=Math.max(0,Math.round(min));const h=Math.floor(min/60),m=min%60;return h?`${h} h ${String(m).padStart(2,'0')} min`:`${m} min`;}
function chargeMinutes(from,to,capacity,peak){
 // Approximate SKia EV9 SOC-dependent curve, calibrated around 10-80% in ~24 minutes.
 const bands=[[0,10,180],[10,40,235],[40,60,180],[60,70,140],[70,80,95],[80,90,55],[90,100,30]];
 let mins=0; for(const [lo,hi,p] of bands){const a=Math.max(from,lo),b=Math.min(to,hi);if(b>a)mins+=(capacity*(b-a)/100)/Math.min(p,peak)*60;}return mins;
}
function calculate(){
 const distance=Math.max(1,n('distance')), speed=Math.max(40,n('speed')), temp=n('temp'), base=Math.max(12,n('base')), capacity=n('capacity'), peak=n('peak'), start=n('start'), reserve=n('reserve'), target=n('target'), overhead=n('overhead'), ascent=n('ascent');
 if(![distance,speed,base,capacity,peak,start,reserve,target,overhead].every(Number.isFinite)||capacity<=0||start<=reserve||target<=reserve||target>100){alert('Please check the trip and battery inputs. Starting charge and charging target must be above the arrival reserve.');return;}
 const speedFactor=speed<=80?0.91+(speed-60)*0.00225:speed<=100?1+(speed-100)*0.0005:1+Math.pow((speed-100)/30,1.55)*0.22;
 const tempFactor=temp>=20?Math.max(.98,1+(temp-20)*.001):1+(20-temp)*.006;
 const terrainFactor={flat:1,rolling:1.05,mountain:1.15}[$('terrain').value];
 const loadFactor={light:.98,normal:1.02,heavy:1.06}[$('load').value];
 const climateFactor={eco:.99,normal:1.015,high:1.05}[$('climate').value];
 const elevationKwh=ascent*2400*9.81/3600000/.9/Math.max(1,distance)*100;
 const elevFactor=Math.max(-.12,Math.min(.45,elevationKwh/base));
 const consumption=base*speedFactor*tempFactor*terrainFactor*loadFactor*climateFactor*(1+elevFactor);
 const tripKwh=distance*consumption/100;
 const range=capacity*(start-reserve)/100/consumption*100;
 const driveMin=distance/speed*60;
 let soc=start, travelled=0, chargeMin=0, stops=[], safety=0;
 // Plan charge stops when remaining energy cannot complete the next leg while preserving reserve.
 while(travelled<distance-0.01 && safety++<100){
   const remaining=distance-travelled;
   const availableKwh=capacity*(soc-reserve)/100;
   const reachableKm=availableKwh/consumption*100;
   if(reachableKm>=remaining){soc-=remaining*consumption/capacity;travelled=distance;break;}
   // Target a charge stop at the last point before the reserve, with a modest 10 km planning buffer.
   const legKm=Math.max(0,Math.min(remaining-1,reachableKm-10));
   if(legKm<1){
      // Charge now if the starting SOC is already close to reserve.
      if(soc<=reserve+0.1){soc=Math.min(target,Math.max(soc+1,target));}
   } else {soc-=legKm*consumption/100/capacity*100;travelled+=legKm;}
   const from=soc, to=Math.max(target, Math.min(90, target));
   if(to<=from+0.1){soc=Math.min(95,from+15);}else{soc=to;}
   const mins=chargeMinutes(from,soc,capacity,peak)+overhead;chargeMin+=mins;
   stops.push({km:Math.round(travelled),from:from,to:soc,mins:mins});
   if(travelled>=distance-0.01)break;
 }
 // If a pathological configuration hits the guard, don't show a fabricated arrival value.
 const arrivalSoc=soc;
 const total=driveMin+chargeMin;
 $('totalTime').textContent=fmtTime(total);$('routeLabel').textContent=`${Math.round(distance)} km${$('origin').value||$('destination').value?' · ':''}${$('origin').value||''}${$('origin').value&&$('destination').value?' → ':''}${$('destination').value||''} · planning estimate`;
 $('driveTime').textContent=fmtTime(driveMin);$('chargeTime').textContent=fmtTime(chargeMin);$('stopCount').textContent=stops.length;
 $('consumption').textContent=consumption.toFixed(1);$('range').textContent=Math.round(range).toLocaleString('en-US');$('energy').textContent=Math.round(tripKwh).toLocaleString('en-US')+' kWh';$('arrival').textContent=Math.max(0,arrivalSoc).toFixed(0)+'%';$('arrivalBar').style.width=Math.max(0,Math.min(100,arrivalSoc))+'%';
 $('arrivalDetail').textContent=arrivalSoc<reserve-0.5?'Below your selected reserve':`Reserve target: ${reserve}%`;
 $('assumptions').textContent=`Consumption estimate includes speed (${speed} km/h), ${temp}°C, ${$('terrain').selectedOptions[0].text.toLowerCase()}, load and climate settings. Elevation adjustment uses net gain only, so it cannot fully model repeated mountain climbs and descents.`;
 const stopEl=$('stops');stopEl.innerHTML='';
 if(!stops.length){stopEl.innerHTML='<p class="muted">No charging stop is estimated for this trip at the selected settings.</p>';}
 else stops.forEach((s,i)=>{const el=document.createElement('div');el.className='stop';el.innerHTML=`<div class="stopnum">${i+1}</div><div><strong>Estimated stop around km ${s.km}</strong><small>Charge from ${s.from.toFixed(0)}% to ${s.to.toFixed(0)}% · no live charger selected</small></div><div class="duration">${fmtTime(s.mins)}<small> incl. overhead</small></div>`;stopEl.appendChild(el);});
}
$('calculate').addEventListener('click',calculate);document.querySelectorAll('input,select').forEach(el=>el.addEventListener('change',()=>{}));calculate();
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/index.html'):
            self.send_error(404)
            return
        body = PAGE.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args))

if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    local_url = f'http://127.0.0.1:{PORT}'
    print(f'SKia EV9 Trip Planner is listening on {HOST}:{PORT}')
    if HOST == "127.0.0.1":
        print(f'Open {local_url} in your browser. Keep this Terminal window open; press Ctrl+C to stop.')
    try:
        if HOST == "127.0.0.1":
            webbrowser.open(local_url)
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping SKia EV9 Trip Planner...')
    finally:
        server.server_close()
