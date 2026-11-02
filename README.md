# Home NILM

Non-intrusive load monitoring for Home Assistant: it learns your appliances from a single whole-house power meter and splits the consumption into per-appliance sensors.

It works with the meter you already have (Shelly EM, Shelly Pro EM, an energy-meter clamp, a P1 meter...) and gets better every time you tell it what an unrecognised load was.

> 🇮🇹 Versione italiana più sotto.

## How it works

1. **Signal.** House consumption (grid + solar) minus every load you already measure (smart plugs, extra clamps). Only what is left is analysed.
2. **Events.** A change in power larger than the minimum step (default 80 W) that lasts at least the persistence time (default 10 s) is an event. Start-up spikes and sensor misalignment are filtered out. Every "off" step is paired with the "on" step it closes, which gives the duration.
3. **Signatures.** Each appliance is described by:
   - active power step (mean and spread);
   - **reactive / active ratio**, when a power factor or reactive power sensor is available. Motors and compressors (fridges, pumps) are clearly inductive; heaters (kettle, toaster, oven, iron) are not;
   - duration;
   - usual hours of use, as a *soft* hint (configurable weight, 0 = off).
4. **Recognition.** Each event is compared with every signature; the closest one within the tolerance wins. Otherwise the event goes to the **unknown events** list.
5. **Learning.** When you label an event, its signature is created or updated. Old samples fade slowly, so a signature follows the appliance if it changes.

## Entities

| Entity | What it is |
|---|---|
| `sensor.<name>` | Power of each learned appliance (W), with its signature in the attributes |
| `sensor.<name>_energy` | Energy of each appliance (kWh, usable in the Energy dashboard) |
| Base load / Other | Always-on consumption (5th percentile over 24 h) and what is left unexplained |
| Variable loads | Optional: unexplained power while e.g. air conditioners run |
| Unmeasured power | The signal being analysed, with diagnostics in the attributes |
| Unknown events | Count of events to label; the list is in the `events` attribute |
| Last event | The latest event and the 10 before it |
| Event to label (select) + What was it? (text) | Label from the dashboard: pick an event (or "What is on now"), type the name |

The `home_nilm_unknown_load` event fires when an unknown load switches on: use it for a notification asking what it was.

## Services

`home_nilm.label_event`, `label_now`, `dismiss_event`, `teach` (seed a signature from known values, e.g. 170 W, ratio 0.3, 14 min), `forget_appliance`, `rename_appliance`, `get_signatures` (returns signatures and unknown events).

## Installation

HACS → Integrations → ⋮ → Custom repositories → add this repository as *Integration* → install **Home NILM** → restart → Settings → Devices & services → Add integration → Home NILM.

## Configuration tips

- **Grid sensor**: positive when importing. With solar, also select the production sensor: the house load is computed as grid + solar.
- **Sub-meters**: add every power sensor that measures a part of the house (plugs, clamps, template sensors for loads you can estimate). The more you subtract, the better the rest is recognised.
- **Power factor**: on a Shelly EM select the power factor sensor of the grid channel. Reactive power is only used while importing from the grid (with solar export the value is not reliable).
- **Variable loads**: select air conditioners, heat pumps or anything with variable power that you cannot measure. While one is active the unexplained power goes to its own category and you are not asked about it.

## Limits

- One meter cannot tell apart two appliances with the same power, the same reactive ratio and the same duration: give them context or a plug.
- Fast-cycling loads (coffee machine boiler, iron thermostat) switch every few seconds and are below the persistence filter: they end up in *Other* or in a sustained-load event.
- Meters that report every 10–15 s hide short events. Readings once a second work much better.

---

## 🇮🇹 In breve

Home NILM impara gli elettrodomestici dal solo contatore generale e crea un sensore di potenza e uno di energia per ognuno.

- Sottrae i carichi che misuri già (prese, pinze) e analizza solo il resto.
- Riconosce le accensioni dal salto di potenza, dalla **potenza reattiva** (i motori, come frigoriferi e pompe, si distinguono dalle resistenze), dalla durata e, se vuoi, dalle ore abituali.
- Quello che non riconosce finisce in **Eventi da riconoscere**. Dalla dashboard scegli l'evento in *Evento da etichettare*, scrivi il nome in *Cos'era?* e da quel momento lo riconosce da solo.
- I condizionatori e gli altri carichi variabili non misurabili vanno in una voce a parte e non generano domande.

Installazione: HACS → Repository personalizzati → aggiungi questo repository come *Integrazione* → installa → riavvia → Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Home NILM.
