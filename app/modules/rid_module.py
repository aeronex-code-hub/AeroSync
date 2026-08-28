import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class RidModule:
    name = "rid"
    MAX_DEVICES = 5
    OFFLINE_SECONDS = 300
    TARGET_OFFLINE_SECONDS = 60
    BRANDS = ("Terjin", "ArcGine")

    def __init__(self, data_dir: Path, get_settings):
        self.data_dir = Path(data_dir)
        self.get_settings = get_settings
        self.lock = threading.RLock()
        self.sources = {}
        self.targets = {}
        self.sessions = {}
        self.message_count = 0
        self.recent_messages = []
        self.status_messages = []
        self.log_dir = self.data_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "rid_capture.log"
        self.log_path.touch(exist_ok=True)
        self.track_log_path = self.log_dir / "rid_tracks.jsonl"
        self.track_log_path.touch(exist_ok=True)
        self.registry_path = self.data_dir / "rid_devices.json"
        self.registry = self._load_registry()

    @staticmethod
    def _first(d, *keys, default=None):
        if not isinstance(d, dict):
            return default
        for key in keys:
            if key in d and d.get(key) not in (None, ""):
                return d.get(key)
        return default

    def _walk_dicts(self, value):
        if isinstance(value, dict):
            yield value
            for v in value.values():
                yield from self._walk_dicts(v)
        elif isinstance(value, list):
            for v in value:
                yield from self._walk_dicts(v)

    def _load_registry(self):
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            rows = data.get("devices") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                return []
            result = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sn = str(row.get("serial_no") or row.get("sn") or "").strip()
                if not sn or sn.lower() in seen:
                    continue
                seen.add(sn.lower())
                brand = str(row.get("brand") or "ArcGine").strip()
                if brand not in self.BRANDS:
                    brand = "ArcGine"
                result.append({
                    "serial_no": sn,
                    "device_name": str(row.get("device_name") or row.get("name") or sn).strip() or sn,
                    "brand": brand,
                    "created_at": str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
                })
            return result[: self.MAX_DEVICES]
        except Exception:
            return []

    def _save_registry(self):
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.registry}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.registry_path)

    def registered_devices(self):
        with self.lock:
            return [dict(x) for x in self.registry]

    def _find_registered(self, sn):
        sn_l = str(sn or "").strip().lower()
        if not sn_l:
            return None
        for row in self.registry:
            if str(row.get("serial_no") or "").strip().lower() == sn_l:
                return row
        return None

    def _clean_brand(self, brand):
        value = str(brand or "").strip()
        if value not in self.BRANDS:
            raise ValueError("Brand must be Terjin or ArcGine")
        return value

    def add_device(self, serial_no, device_name, brand):
        sn = str(serial_no or "").strip()
        name = str(device_name or "").strip()
        brand = self._clean_brand(brand)
        if not sn:
            raise ValueError("Serial No. is required")
        if not name:
            raise ValueError("Device Name is required")
        if len(sn) > 128 or len(name) > 128:
            raise ValueError("RID device value is too long")
        with self.lock:
            if self._find_registered(sn):
                raise ValueError("RID device serial number already exists")
            if len(self.registry) >= self.MAX_DEVICES:
                raise ValueError("Maximum 5 RID devices can be registered")
            row = {"serial_no": sn, "device_name": name, "brand": brand, "created_at": datetime.now(timezone.utc).isoformat()}
            self.registry.append(row)
            self._save_registry()
            return dict(row)

    def update_device(self, serial_no, new_serial_no, device_name, brand):
        old_sn = str(serial_no or "").strip()
        new_sn = str(new_serial_no or old_sn).strip()
        name = str(device_name or "").strip()
        brand = self._clean_brand(brand)
        if not old_sn or not new_sn or not name:
            raise ValueError("Serial No. and Device Name are required")
        if len(new_sn) > 128 or len(name) > 128:
            raise ValueError("RID device value is too long")
        with self.lock:
            row = self._find_registered(old_sn)
            if not row:
                raise ValueError("RID device not found")
            conflict = self._find_registered(new_sn)
            if conflict and conflict is not row:
                raise ValueError("RID device serial number already exists")
            row["serial_no"] = new_sn
            row["device_name"] = name
            row["brand"] = brand
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            if old_sn.lower() != new_sn.lower() and old_sn in self.sources:
                src = self.sources.pop(old_sn)
                src["id"] = new_sn
                src["serial_no"] = new_sn
                src["name"] = name
                src["brand"] = brand
                self.sources[new_sn] = src
            elif new_sn in self.sources:
                self.sources[new_sn]["name"] = name
                self.sources[new_sn]["brand"] = brand
            self._save_registry()
            return dict(row)

    def remove_device(self, serial_no):
        sn = str(serial_no or "").strip()
        if not sn:
            raise ValueError("Serial No. is required")
        with self.lock:
            row = self._find_registered(sn)
            if not row:
                raise ValueError("RID device not found")
            self.registry = [x for x in self.registry if x is not row]
            self.sources.pop(str(row.get("serial_no") or ""), None)
            remove_ids = [uid for uid, target in self.targets.items() if str(target.get("source_id") or "").lower() == sn.lower()]
            for uid in remove_ids:
                self.targets.pop(uid, None)
            self._save_registry()
            return True

    def _identity_candidates(self, topic, payload):
        candidates = set()
        parts = [p for p in str(topic or "").split("/") if p]
        # DJI/FH2-style product topic.
        if len(parts) >= 3 and parts[0].lower() == "thing" and parts[1].lower() == "product":
            candidates.add(str(parts[2]).strip())
        # ArcGine RGS topics always end with the registered receiver SN, including
        # receiver_json_astm messages whose JSON body intentionally has no RGS SN.
        if len(parts) >= 3 and parts[0].lower() == "device":
            candidates.add(str(parts[-1]).strip())
        for d in self._walk_dicts(payload):
            for key in ("sn", "SN", "device_sn", "DeviceSn", "DeviceSN", "serial_no", "SerialNo", "DeviceId", "DeviceID", "device_id"):
                value = d.get(key) if isinstance(d, dict) else None
                if value not in (None, ""):
                    candidates.add(str(value).strip())
        return {x for x in candidates if x}

    def _match_registered(self, topic, payload):
        candidates = {x.lower() for x in self._identity_candidates(topic, payload)}
        terjin_topics = set()
        for d in self._walk_dicts(payload):
            value = self._first(d, "Topic", "topic")
            if value not in (None, ""):
                terjin_topics.add(str(value).strip().lower())

        for row in self.registry:
            registered_id = str(row.get("serial_no") or "").strip().lower()
            if not registered_id:
                continue
            brand = str(row.get("brand") or "ArcGine")
            if brand == "Terjin":
                # Terjin does not expose a receiver serial number in the documented
                # /api/system payload.  The configured device Topic is therefore the
                # registration/matching value entered in the existing Serial No. field.
                if registered_id in terjin_topics:
                    return row
                # Keep DeviceId/legacy identity matching for existing registrations.
                if registered_id in candidates:
                    return row
            elif registered_id in candidates:
                return row
        return None

    def _source_info(self, topic, payload, registered):
        sn = str(registered.get("serial_no") or "")
        brand = str(registered.get("brand") or "ArcGine")
        topic_text = str(topic or "")
        now = datetime.now(timezone.utc).isoformat()

        if brand == "Terjin":
            source = {}
            # Primary Terjin matching is by the configured Topic value.  The UI field
            # remains named Serial No. so no frontend/database format change is needed.
            for d in self._walk_dicts(payload):
                source_topic = self._first(d, "Topic", "topic")
                if source_topic not in (None, "") and str(source_topic).strip().lower() == sn.lower():
                    source = d
                    break
            # Backward compatibility for Terjin devices previously registered using
            # DeviceId (for example "600").
            if not source:
                for d in self._walk_dicts(payload):
                    device_id = self._first(d, "DeviceId", "DeviceID", "device_id")
                    if device_id not in (None, "") and str(device_id).strip().lower() == sn.lower():
                        source = d
                        break
            return {
                "id": sn, "serial_no": sn, "name": str(registered.get("device_name") or sn),
                "brand": brand, "status": "online", "last_seen": now, "topic": topic_text,
                "device_type": str(self._first(source, "CoDeviceType", "DeviceType", default="") or ""),
                "device_version": str(self._first(source, "DeviceVersion", default="") or ""),
                "lat": self._first(source, "Latitude", "latitude", "Lat"),
                "lng": self._first(source, "Longitude", "longitude", "Lon", "Lng"),
                "altitude": self._first(source, "Altitude", "altitude", "Alt"),
                "gps_number": self._first(source, "GpsStalliteNum", "GpsSatelliteNum", default=None),
                "gps_fixed": self._first(source, "GpsLocked", default=None),
                "temperature": self._first(source, "Temperature", default=None),
                "voltage": self._first(source, "Voltage", default=None),
                "yaw": self._first(source, "Yaw", default=None),
                "pitch": self._first(source, "Pitch", default=None),
            }

        # ArcGine documented RGS MQTT interface.
        if "/receiver_heart/" in topic_text:
            return {
                "id": sn, "serial_no": sn, "name": str(registered.get("device_name") or sn),
                "brand": brand, "status": "online", "last_seen": now, "last_heartbeat": now,
                "topic": topic_text, "simcard": self._first(payload, "simcard"),
                "rssi": self._first(payload, "rssi"), "product_id": self._first(payload, "productid"),
                "heartbeat_no": self._first(payload, "heartno"), "connection_type": self._first(payload, "conn_type"),
                "ota_state": self._first(payload, "ota_state"), "battery_percent": self._first(payload, "bat_state"),
                "device_version": str(self._first(payload, "sys_ver", default="") or ""),
            }
        if "/receiver_pos/" in topic_text:
            gps = payload.get("GPS") if isinstance(payload, dict) and isinstance(payload.get("GPS"), dict) else {}
            lat = self._first(gps, "latitude", "Lat")
            lng = self._first(gps, "longitude", "Lng", "Lon")
            try:
                if float(lat or 0) == 0 and float(lng or 0) == 0: lat = lng = None
            except Exception:
                lat = lng = None
            return {
                "id": sn, "serial_no": sn, "name": str(registered.get("device_name") or sn),
                "brand": brand, "status": "online", "last_seen": now, "topic": topic_text,
                "lat": lat, "lng": lng, "altitude": self._first(gps, "altitude"),
                "gps_fixed": self._first(gps, "fix"), "gps_number": self._first(gps, "nsat"),
                "gps_hdop": self._first(gps, "HDOP"), "heading": self._first(gps, "COG"),
                "ground_speed_kmh": self._first(gps, "spkm"), "ground_speed_knots": self._first(gps, "spkn"),
                "gps_utc": self._first(gps, "UTC"), "gps_date": self._first(gps, "date"),
            }
        if topic_text.startswith("device/"):
            return {"id": sn, "serial_no": sn, "name": str(registered.get("device_name") or sn), "brand": brand, "last_seen": now, "topic": topic_text}

        # Backward-compatible generic ArcGine/DJI-style OSD data. This is retained so
        # existing deployed receivers continue to expose health fields if they publish them.
        data = payload.get("data") if isinstance(payload, dict) else None
        host = data.get("host") if isinstance(data, dict) and isinstance(data.get("host"), dict) else {}
        lat = self._first(host, "latitude", "Latitude", "lat", "Lat")
        lng = self._first(host, "longitude", "Longitude", "lng", "Lng", "lon", "Lon")
        altitude = self._first(host, "height", "Height", "altitude", "Altitude")
        try:
            if float(lat or 0) == 0 and float(lng or 0) == 0: lat = lng = None
        except Exception:
            lat = lng = None
        battery = host.get("battery") if isinstance(host.get("battery"), dict) else {}
        network = host.get("network_state") if isinstance(host.get("network_state"), dict) else {}
        position = host.get("position_state") if isinstance(host.get("position_state"), dict) else {}
        storage = host.get("storage") if isinstance(host.get("storage"), dict) else {}
        sub = host.get("sub_device") if isinstance(host.get("sub_device"), dict) else {}
        return {
            "id": sn, "serial_no": sn, "name": str(registered.get("device_name") or sn), "brand": brand,
            "status": "online", "last_seen": now, "topic": topic_text,
            "biz_code": str(payload.get("biz_code") or "") if isinstance(payload, dict) else "",
            "lat": lat, "lng": lng, "altitude": altitude, "heading": self._first(host, "heading", "Heading"),
            "country": self._first(host, "country", "Country", default=""),
            "battery_percent": self._first(battery, "capacity_percent", "capacity", default=None),
            "battery_voltage": self._first(battery, "voltage", default=self._first(host, "battery_voltage")),
            "battery_temperature": self._first(battery, "temperature", default=None),
            "network_type": self._first(network, "type", default=None), "network_quality": self._first(network, "quality", default=None),
            "network_rate": self._first(network, "rate", default=None), "gps_number": self._first(position, "gps_number", default=None),
            "rtk_number": self._first(position, "rtk_number", default=None), "gps_fixed": self._first(position, "is_fixed", default=None),
            "poe_status": self._first(host, "poe_status", default=None), "power_mode": self._first(host, "power_mode", "electric_supply_mode", default=None),
            "storage_total": self._first(storage, "total", default=None), "storage_used": self._first(storage, "used", default=None),
            "device_paired": self._first(sub, "device_paired", default=None), "sub_device_online_status": self._first(sub, "device_online_status", default=None),
            "mode_code": self._first(host, "mode_code", "flighttask_step_code", default=None),
        }

    def _targets(self, topic, payload, brand):
        found = []
        topic_text = str(topic or "")
        if brand == "ArcGine" and "/receiver_json_astm/" in topic_text and isinstance(payload, dict):
            uid = self._first(payload, "uas_id")
            lat = self._first(payload, "latitude")
            lng = self._first(payload, "longitude")
            if uid not in (None, "") and lat not in (None, "") and lng not in (None, ""):
                found.append((str(uid), payload, "arcgine_astm"))
            return found
        if brand == "ArcGine" and "/adsb/" in topic_text:
            # ADS-B is deliberately kept separate from Remote ID aircraft counts/tracks.
            return found
        for d in self._walk_dicts(payload):
            uid = self._first(d, "UavId", "uav_id", "uas_id", "aircraft_id", "rid_id")
            lat = self._first(d, "UavLat", "uav_lat")
            lng = self._first(d, "UavLon", "uav_lon")
            if uid in (None, ""):
                continue
            if brand == "Terjin":
                # /api/detect reports a valid aircraft before UAV coordinates are
                # available.  Create/refresh the track immediately; /api/locate can
                # enrich the same UavId with position later.
                found.append((str(uid), d, "terjin"))
            elif lat not in (None, "") and lng not in (None, ""):
                found.append((str(uid), d, "generic"))
        return found

    def handle_mqtt(self, item, raw_payload=None):
        try:
            payload = raw_payload if isinstance(raw_payload, (dict, list)) else json.loads(str(raw_payload if raw_payload is not None else item.get("payload") or "{}"))
        except Exception:
            return False
        with self.lock:
            registered = self._match_registered(item.get("topic"), payload)
            if not registered:
                return False
            sn = str(registered.get("serial_no") or "")
            brand = str(registered.get("brand") or "ArcGine")
            now = datetime.now(timezone.utc).isoformat()
            self.message_count += 1
            incoming = self._source_info(item.get("topic"), payload, registered)
            old_source = self.sources.get(sn, {})
            # Keep previously learned values when a later OSD payload omits them.
            merged = dict(old_source)
            for key, value in incoming.items():
                if value not in (None, "") or key in ("status", "last_seen", "topic", "biz_code", "name"):
                    merged[key] = value
            self.sources[sn] = merged

            for uid, d, parser_kind in self._targets(item.get("topic"), payload, brand):
                old = self.targets.get(uid, {})
                if parser_kind == "arcgine_astm":
                    known = {"uas_id","uas_id_type","ua_type","latitude","longitude","geodetic_altitude","pressure_altitude","timestamp","timestamp_accuracy","baromete_accuracy","vertical_accuracy","horizontal_accuracy","speed_accuracy","vertical_speed","speed","track_direction","height","height_type","operational_status","operation_description","operator_id","auth_Data","operator_latitude","operator_longitude","operator_altitude","operator_location_type","operating_area_radius","operator_category","operator_class","operator_classification","operating_area_count","operating_area_floor","operating_area_ceiling"}
                    target = {
                        "id": uid, "uav_id": uid, "model": str(old.get("model") or ""),
                        "uas_id_type": self._first(d,"uas_id_type"), "ua_type": self._first(d,"ua_type"),
                        "lat": self._first(d,"latitude"), "lng": self._first(d,"longitude"),
                        "altitude": self._first(d,"geodetic_altitude"), "pressure_altitude": self._first(d,"pressure_altitude"),
                        "height": self._first(d,"height"), "height_type": self._first(d,"height_type"),
                        "speed": self._first(d,"speed"), "vertical_speed": self._first(d,"vertical_speed"),
                        "heading": self._first(d,"track_direction"), "operational_status": self._first(d,"operational_status"),
                        "operation_description": self._first(d,"operation_description",default=""),
                        "user_id": str(self._first(d,"operator_id",default="") or ""),
                        "pilot_lat": self._first(d,"operator_latitude"), "pilot_lng": self._first(d,"operator_longitude"),
                        "operator_altitude": self._first(d,"operator_altitude"), "operator_location_type": self._first(d,"operator_location_type"),
                        "auth_data": self._first(d,"auth_Data"), "rid_timestamp": self._first(d,"timestamp"),
                        "timestamp_accuracy": self._first(d,"timestamp_accuracy"), "barometric_accuracy": self._first(d,"baromete_accuracy"),
                        "vertical_accuracy": self._first(d,"vertical_accuracy"), "horizontal_accuracy": self._first(d,"horizontal_accuracy"),
                        "speed_accuracy": self._first(d,"speed_accuracy"), "operating_area_radius": self._first(d,"operating_area_radius"),
                        "operator_category": self._first(d,"operator_category"), "operator_class": self._first(d,"operator_class"),
                        "operator_classification": self._first(d,"operator_classification"), "operating_area_count": self._first(d,"operating_area_count"),
                        "operating_area_floor": self._first(d,"operating_area_floor"), "operating_area_ceiling": self._first(d,"operating_area_ceiling"),
                        "additional_data": {k:v for k,v in d.items() if k not in known},
                    }
                else:
                    target = {
                        "id": uid, "uav_id": uid,
                        "model": str(self._first(d, "UavModel", "UavModelText", "uav_model", "model", default=old.get("model") or "") or ""),
                        "model_no": self._first(d, "UavModelNo", "uav_model_no", default=old.get("model_no")),
                        # Keep the last valid UAV position when /api/detect messages
                        # arrive without UavLat/UavLon. SensorLatitude/Longitude are
                        # receiver coordinates and must never be used as UAV position.
                        "lat": self._first(d, "UavLat", "uav_lat", default=old.get("lat")),
                        "lng": self._first(d, "UavLon", "uav_lon", default=old.get("lng")),
                        "altitude": self._first(d, "UavAlt", "uav_alt", "altitude", "Alt"), "height": self._first(d, "UavHeight", "uav_height", "height"),
                        "speed": self._first(d, "Velocity", "velocity", "speed"), "heading": self._first(d, "Yaw", "yaw", "heading"),
                        "pilot_lat": self._first(d, "PilotLat", "pilot_lat"), "pilot_lng": self._first(d, "PilotLon", "pilot_lon", "PilotLng"),
                        "home_lat": self._first(d, "HomeLat", "home_lat"), "home_lng": self._first(d, "HomeLon", "home_lon", "HomeLng"),
                        "trace_id": str(self._first(d, "TraceId", "trace_id", default="") or ""),
                        "user_id": str(self._first(d, "UserId", "user_id", "operator_id", default="") or ""),
                        "frequency": self._first(d, "Frequency", "frequency"), "band": self._first(d, "Band", "band"),
                        "distance": self._first(d, "UavDistance", "uav_distance", "Distance", "distance"),
                        "azimuth": self._first(d, "UavAzimuth", "uav_azimuth", "Azimuth", "azimuth"),
                        "rssi": self._first(d, "Rssi", "RSSI", "rssi"), "snr": self._first(d, "SNR", "snr"),
                        "confidence": self._first(d, "Papr", "papr", "confidence"), "start_from": self._first(d, "StartFrom", "start_from"),
                        "duration": self._first(d, "Duration", "duration"), "area_flag": self._first(d, "AreaFlag", "area_flag"),
                        "whitelist_id": self._first(d, "WhiteListId", "WhitelistId", "whitelist_id"), "image": self._first(d, "Image", "image"),
                    }
                seen_by = list(old.get("seen_by") or [])
                if sn not in seen_by: seen_by.append(sn)
                target.update({
                    "source_id": sn, "source_name": str(registered.get("device_name") or sn), "source_brand": brand,
                    "seen_by": seen_by[-10:], "topic": str(item.get("topic") or ""),
                    "first_seen": old.get("first_seen", now), "last_seen": now, "status": "live",
                })
                trail = list(old.get("trail") or [])
                try:
                    point = {"lat": float(target["lat"]), "lng": float(target["lng"]), "time": now}
                    if not trail or trail[-1].get("lat") != point["lat"] or trail[-1].get("lng") != point["lng"]:
                        trail.append(point)
                except Exception:
                    pass
                target["trail"] = trail[-1000:]
                track_id = target.get("trace_id") or old.get("track_id") or f"{sn}:{uid}:{str(target.get('first_seen') or now)[:19]}"
                target["track_id"] = track_id
                self.targets[uid] = {**old, **target}
                track_record = {k: v for k, v in self.targets[uid].items() if k != "trail"}
                track_record["recorded_at"] = now
                try:
                    with self.track_log_path.open("a", encoding="utf-8") as tf:
                        tf.write(json.dumps(track_record, ensure_ascii=False, separators=(",", ":")) + "\n")
                except Exception:
                    pass

            record = {"received_at": now, "source_sn": sn, "device_name": registered.get("device_name"), "brand": brand, "topic": item.get("topic"), "payload": payload}
            message_id = self.message_count
            recent = {
                "id": message_id, "received_at": now, "source_sn": sn,
                "device_name": str(registered.get("device_name") or sn), "brand": brand,
                "topic": str(item.get("topic") or ""), "bytes": int(item.get("bytes") or 0),
                "payload_type": str(item.get("payload_type") or "JSON"), "payload": payload,
                "message_type": str(payload.get("biz_code") or payload.get("type") or payload.get("method") or "RID") if isinstance(payload, dict) else "RID",
            }
            self.recent_messages.append(recent)
            self.recent_messages = self.recent_messages[-200:]
            detected_now = [t for t in self.targets.values() if t.get("source_id") == sn and t.get("last_seen") == now]
            if detected_now:
                for t in detected_now:
                    self.status_messages.append({"time": now, "level": "ok", "title": "Aircraft Detected", "message": f"{t.get('model') or 'UAV'} | {t.get('uav_id') or t.get('id')} | {registered.get('device_name') or sn}"})
            else:
                self.status_messages.append({"time": now, "level": "info", "title": "RID Receiver Online", "message": f"{registered.get('device_name') or sn} | {recent['message_type']}"})
            self.status_messages = self.status_messages[-100:]
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        # A registered RID source owns this MQTT message; do not duplicate it in normal MQTT history/UI.
        return True

    def status(self):
        now = time.time()
        with self.lock:
            sources = []
            for registered in self.registry:
                sn = str(registered.get("serial_no") or "")
                src = dict(self.sources.get(sn) or {})
                src.setdefault("id", sn)
                src.setdefault("serial_no", sn)
                src["name"] = str(registered.get("device_name") or sn)
                src["brand"] = str(registered.get("brand") or "ArcGine")
                last_seen = src.get("last_seen")
                online = False
                health_seen = src.get("last_heartbeat") if src.get("brand") == "ArcGine" and src.get("last_heartbeat") else last_seen
                if health_seen:
                    try:
                        online = (now - datetime.fromisoformat(str(health_seen)).timestamp()) <= self.OFFLINE_SECONDS
                    except Exception:
                        online = False
                src["status"] = "online" if online else "offline"
                sources.append(src)

            # Live aircraft are intentionally short-lived. If the RID receiver stops
            # reporting a UAV for 60 seconds, remove it from the live state. The
            # historical JSONL records remain available through RID History.
            expired_ids = []
            targets = []
            for uid, target in self.targets.items():
                t = dict(target)
                try:
                    age = now - datetime.fromisoformat(str(t.get("last_seen") or "")).timestamp()
                except Exception:
                    age = self.TARGET_OFFLINE_SECONDS + 1
                if age > self.TARGET_OFFLINE_SECONDS:
                    expired_ids.append(uid)
                    continue
                t["status"] = "live"
                targets.append(t)
            for uid in expired_ids:
                self.targets.pop(uid, None)

            return {
                "ok": True,
                "message_count": self.message_count,
                "source_count": len(sources),
                "online_sources": sum(1 for s in sources if s.get("status") == "online"),
                "target_count": len(targets),
                "active_targets": len(targets),
                "offline_sources": sum(1 for s in sources if s.get("status") != "online"),
                "active_tracks": len({str(t.get("track_id") or t.get("trace_id") or t.get("id")) for t in targets}),
                "max_devices": self.MAX_DEVICES,
                "offline_seconds": self.OFFLINE_SECONDS,
                "target_offline_seconds": self.TARGET_OFFLINE_SECONDS,
                "sources": sorted(sources, key=lambda x: x.get("last_seen", ""), reverse=True),
                "targets": sorted(targets, key=lambda x: x.get("last_seen", ""), reverse=True),
                "recent_messages": list(reversed(self.recent_messages[-20:])),
                "live_messages": list(reversed(self.recent_messages[-12:])),
                "status_messages": list(reversed(self.status_messages[-12:])),
            }

    def raw_search(self, q="", source="", limit=100):
        q = str(q or "").strip().lower()
        source = str(source or "").strip().lower()
        limit = max(1, min(1000, int(limit or 100)))
        rows = []
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10000:]
        except Exception:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if source and source not in str(row.get("source_sn") or "").lower() and source not in str(row.get("device_name") or "").lower():
                continue
            hay = json.dumps(row, ensure_ascii=False).lower()
            if q and q not in hay:
                continue
            rows.append(row)
        return {"ok": True, "count": len(rows), "messages": list(reversed(rows[-limit:]))}

    def track_history(self, q="", source="", limit=250):
        q = str(q or "").strip().lower()
        source = str(source or "").strip().lower()
        limit = max(1, min(1000, int(limit or 250)))
        latest = {}
        counts = {}
        first = {}
        try:
            lines = self.track_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-50000:]
        except Exception:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except Exception:
                continue
            tid = str(row.get("track_id") or row.get("trace_id") or "").strip()
            if not tid:
                continue
            if source and source not in str(row.get("source_id") or "").lower() and source not in str(row.get("source_name") or "").lower():
                continue
            hay = json.dumps(row, ensure_ascii=False).lower()
            if q and q not in hay:
                continue
            latest[tid] = row
            counts[tid] = counts.get(tid, 0) + 1
            first.setdefault(tid, row.get("first_seen") or row.get("recorded_at"))
        rows=[]
        for tid,row in latest.items():
            x=dict(row); x["track_id"]=tid; x["point_count"]=counts.get(tid,0); x["history_start"]=first.get(tid); rows.append(x)
        rows.sort(key=lambda x: x.get("recorded_at") or x.get("last_seen") or "", reverse=True)
        return {"ok": True, "count": len(rows), "tracks": rows[:limit]}

    def track_details(self, track_id):
        tid = str(track_id or "").strip()
        rows=[]
        try:
            lines = self.track_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-50000:]
        except Exception:
            lines=[]
        for line in lines:
            try:
                row=json.loads(line)
            except Exception:
                continue
            if str(row.get("track_id") or row.get("trace_id") or "") == tid:
                rows.append(row)
        return {"ok": True, "track_id": tid, "count": len(rows), "summary": (rows[-1] if rows else {}), "points": rows}
