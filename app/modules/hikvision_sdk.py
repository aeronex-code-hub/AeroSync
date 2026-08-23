import ctypes
import os
import sys
import time
from urllib.parse import urlsplit
from ctypes import POINTER, byref, c_byte, c_char, c_char_p, c_int, c_long, c_uint, c_ushort, c_void_p
from pathlib import Path


HIKVISION_ERROR_MESSAGES = {
    1: "Username or password error",
    2: "Insufficient permission",
    3: "SDK not initialized",
    4: "Channel number error",
    5: "Too many connections",
    6: "SDK version mismatch",
    7: "Network connection failed",
    8: "Network send timeout",
    9: "Network receive timeout",
    10: "Network receive data error",
    11: "SDK unsupported operation",
    12: "Call order error",
    17: "Parameter error",
    23: "Unsupported function",
    29: "Device operation failed",
    34: "Create file error",
    43: "No buffer",
    47: "User does not exist",
    52: "Device already logged in",
    73: "Socket closed by peer",
    76: "Program exception",
    153: "User locked",
    154: "User forbidden",
    155: "User expired",
}

DESC_LEN = 16
DEV_ID_LEN = 32
MAX_CHANNUM_V30 = 64
MAX_DOMAIN_NAME = 64
MAX_IP_DEVICE_V40 = 64
MAX_PRO_PATH = 256
NAME_LEN = 32
NET_DVR_GET_IPPARACFG_V40 = 1062
NET_DVR_SET_IPPARACFG_V40 = 1063
NET_DVR_SET_CUSTOM_PRO_CFG = 6117
NET_SDK_IP_DEVICE = 0
NET_SDK_STREAM_MEDIA_URL = 4
NET_SDK_INIT_CFG_TYPE_CHECK_MODULE_COM = 0
NET_SDK_INIT_CFG_SDK_PATH = 2
NET_SDK_INIT_CFG_LIBEAY_PATH = 3
NET_SDK_INIT_CFG_SSLEAY_PATH = 4
NET_SDK_MAX_FILE_PATH = 256
PASSWD_LEN = 16
SERIALNO_LEN = 48
URL_LEN = 240
IPC_PROTOCOL_NUM = 50
MAX_CUSTOM_PROTOCOL_SLOT = 16


def _set_byte_array(target, value, limit=None):
    raw = str(value or "").encode("utf-8")
    max_len = min(len(target), limit or len(target))
    for index in range(len(target)):
        target[index] = 0
    for index, byte in enumerate(raw[: max_len - 1]):
        target[index] = byte


def _byte_array_text(value):
    return bytes(value).split(b"\x00", 1)[0].decode("utf-8", errors="ignore")



def _custom_protocol_slot(channel):
    try:
        value = int(channel)
    except (TypeError, ValueError):
        value = 1
    if value < 1:
        value = 1
    return ((value - 1) % MAX_CUSTOM_PROTOCOL_SLOT) + 1

def _is_ipv4(value):
    parts = str(value or "").split(".")
    if len(parts) != 4:
        return False
    try:
        return all(str(int(part)) == part and 0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


class NET_DVR_DEVICEINFO_V30(ctypes.Structure):
    _fields_ = [
        ("sSerialNumber", c_byte * 48),
        ("byAlarmInPortNum", c_byte),
        ("byAlarmOutPortNum", c_byte),
        ("byDiskNum", c_byte),
        ("byDVRType", c_byte),
        ("byChanNum", c_byte),
        ("byStartChan", c_byte),
        ("byAudioChanNum", c_byte),
        ("byIPChanNum", c_byte),
        ("byZeroChanNum", c_byte),
        ("byMainProto", c_byte),
        ("bySubProto", c_byte),
        ("bySupport", c_byte),
        ("bySupport1", c_byte),
        ("bySupport2", c_byte),
        ("wDevType", c_ushort),
        ("bySupport3", c_byte),
        ("byMultiStreamProto", c_byte),
        ("byStartDChan", c_byte),
        ("byStartDTalkChan", c_byte),
        ("byHighDChanNum", c_byte),
        ("bySupport4", c_byte),
        ("byLanguageType", c_byte),
        ("byVoiceInChanNum", c_byte),
        ("byStartVoiceInChanNo", c_byte),
        ("bySupport5", c_byte),
        ("bySupport6", c_byte),
        ("byMirrorChanNum", c_byte),
        ("wStartMirrorChanNo", c_ushort),
        ("bySupport7", c_byte),
        ("byRes2", c_byte * 2),
    ]


class NET_DVR_CUSTOM_PROTOCAL(ctypes.Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("dwEnabled", c_uint),
        ("sProtocalName", c_char * DESC_LEN),
        ("byRes1", c_byte * 64),
        ("dwEnableSubStream", c_uint),
        ("byMainProType", c_byte),
        ("byMainTransType", c_byte),
        ("wMainPort", c_ushort),
        ("sMainPath", c_char * MAX_PRO_PATH),
        ("bySubProType", c_byte),
        ("bySubTransType", c_byte),
        ("wSubPort", c_ushort),
        ("sSubPath", c_char * MAX_PRO_PATH),
        ("byRes2", c_byte * 200),
    ]


class NET_DVR_PROTO_TYPE(ctypes.Structure):
    _fields_ = [
        ("dwType", c_uint),
        ("byDescribe", c_byte * DESC_LEN),
    ]


class NET_DVR_IPC_PROTO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("dwProtoNum", c_uint),
        ("struProto", NET_DVR_PROTO_TYPE * IPC_PROTOCOL_NUM),
        ("byRes", c_byte * 8),
    ]


class NET_DVR_IPADDR(ctypes.Structure):
    _fields_ = [
        ("sIpV4", c_char * 16),
        ("byIPv6", c_byte * 128),
    ]


class NET_DVR_IPDEVINFO_V31(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byProType", c_byte),
        ("byEnableQuickAdd", c_byte),
        ("byCameraType", c_byte),
        ("sUserName", c_byte * NAME_LEN),
        ("sPassword", c_byte * PASSWD_LEN),
        ("byDomain", c_byte * MAX_DOMAIN_NAME),
        ("struIP", NET_DVR_IPADDR),
        ("wDVRPort", c_ushort),
        ("szDeviceID", c_byte * 32),
        ("byEnableTiming", c_byte),
        ("byCertificateValidation", c_byte),
    ]


class NET_DVR_IPCHANINFO(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byIPID", c_byte),
        ("byChannel", c_byte),
        ("byIPIDHigh", c_byte),
        ("byTransProtocol", c_byte),
        ("byGetStream", c_byte),
        ("byres", c_byte * 30),
    ]


class NET_DVR_IPSERVER_STREAM(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byRes", c_byte * 3),
        ("struIPServer", NET_DVR_IPADDR),
        ("wPort", c_ushort),
        ("wDvrNameLen", c_ushort),
        ("byDVRName", c_byte * NAME_LEN),
        ("wDVRSerialLen", c_ushort),
        ("byRes1", c_ushort * 2),
        ("byDVRSerialNumber", c_byte * SERIALNO_LEN),
        ("byUserName", c_byte * NAME_LEN),
        ("byPassWord", c_byte * PASSWD_LEN),
        ("byChannel", c_byte),
        ("byRes2", c_byte * 11),
    ]


class NET_DVR_STREAM_MEDIA_SERVER_CFG(ctypes.Structure):
    _fields_ = [
        ("byValid", c_byte),
        ("byRes1", c_byte * 3),
        ("struDevIP", NET_DVR_IPADDR),
        ("wDevPort", c_ushort),
        ("byTransmitType", c_byte),
        ("byRes2", c_byte * 69),
    ]


class NET_DVR_DEV_CHAN_INFO(ctypes.Structure):
    _fields_ = [
        ("struIP", NET_DVR_IPADDR),
        ("wDVRPort", c_ushort),
        ("byChannel", c_byte),
        ("byTransProtocol", c_byte),
        ("byTransMode", c_byte),
        ("byFactoryType", c_byte),
        ("byDeviceType", c_byte),
        ("byDispChan", c_byte),
        ("bySubDispChan", c_byte),
        ("byResolution", c_byte),
        ("byRes", c_byte * 2),
        ("byDomain", c_byte * MAX_DOMAIN_NAME),
        ("sUserName", c_byte * NAME_LEN),
        ("sPassword", c_byte * PASSWD_LEN),
    ]


class NET_DVR_PU_STREAM_CFG(ctypes.Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("struStreamMediaSvrCfg", NET_DVR_STREAM_MEDIA_SERVER_CFG),
        ("struDevChanInfo", NET_DVR_DEV_CHAN_INFO),
    ]


class NET_DVR_DDNS_STREAM_CFG(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byRes1", c_byte * 3),
        ("struStreamServer", NET_DVR_IPADDR),
        ("wStreamServerPort", c_ushort),
        ("byStreamServerTransmitType", c_byte),
        ("byRes2", c_byte),
        ("struIPServer", NET_DVR_IPADDR),
        ("wIPServerPort", c_ushort),
        ("byRes3", c_byte * 2),
        ("sDVRName", c_byte * NAME_LEN),
        ("wDVRNameLen", c_ushort),
        ("wDVRSerialLen", c_ushort),
        ("sDVRSerialNumber", c_byte * SERIALNO_LEN),
        ("sUserName", c_byte * NAME_LEN),
        ("sPassWord", c_byte * PASSWD_LEN),
        ("wDVRPort", c_ushort),
        ("byRes4", c_byte * 2),
        ("byChannel", c_byte),
        ("byTransProtocol", c_byte),
        ("byTransMode", c_byte),
        ("byFactoryType", c_byte),
    ]


class NET_DVR_PU_STREAM_URL(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("strURL", c_byte * URL_LEN),
        ("byTransPortocol", c_byte),
        ("wIPID", c_ushort),
        ("byChannel", c_byte),
        ("byRes", c_byte * 7),
    ]


class NET_DVR_HKDDNS_STREAM(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byRes", c_byte * 3),
        ("byDDNSDomain", c_byte * 64),
        ("wPort", c_ushort),
        ("wAliasLen", c_ushort),
        ("byAlias", c_byte * NAME_LEN),
        ("wDVRSerialLen", c_ushort),
        ("byRes1", c_byte * 2),
        ("byDVRSerialNumber", c_byte * SERIALNO_LEN),
        ("byUserName", c_byte * NAME_LEN),
        ("byPassWord", c_byte * PASSWD_LEN),
        ("byChannel", c_byte),
        ("byRes2", c_byte * 11),
    ]


class NET_DVR_IPCHANINFO_V40(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byRes1", c_byte),
        ("wIPID", c_ushort),
        ("dwChannel", c_uint),
        ("byTransProtocol", c_byte),
        ("byTransMode", c_byte),
        ("byFactoryType", c_byte),
        ("byRes", c_byte),
        ("strURL", c_byte * URL_LEN),
    ]


class NET_DVR_GET_STREAM_UNION(ctypes.Union):
    _fields_ = [
        ("struChanInfo", NET_DVR_IPCHANINFO),
        ("struIPServerStream", NET_DVR_IPSERVER_STREAM),
        ("struPUStream", NET_DVR_PU_STREAM_CFG),
        ("struDDNSStream", NET_DVR_DDNS_STREAM_CFG),
        ("struStreamUrl", NET_DVR_PU_STREAM_URL),
        ("struHkDDNSStream", NET_DVR_HKDDNS_STREAM),
        ("struIPChan", NET_DVR_IPCHANINFO_V40),
    ]


class NET_DVR_STREAM_MODE(ctypes.Structure):
    _fields_ = [
        ("byGetStreamType", c_byte),
        ("byRes", c_byte * 3),
        ("uGetStream", NET_DVR_GET_STREAM_UNION),
    ]


class NET_DVR_IPPARACFG_V40(ctypes.Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("dwGroupNum", c_uint),
        ("dwAChanNum", c_uint),
        ("dwDChanNum", c_uint),
        ("dwStartDChan", c_uint),
        ("byAnalogChanEnable", c_byte * MAX_CHANNUM_V30),
        ("struIPDevInfo", NET_DVR_IPDEVINFO_V31 * MAX_IP_DEVICE_V40),
        ("struStreamMode", NET_DVR_STREAM_MODE * MAX_CHANNUM_V30),
        ("byRes2", c_byte * 20),
    ]


class NET_DVR_LOCAL_SDK_PATH(ctypes.Structure):
    _fields_ = [
        ("sPath", c_char * NET_SDK_MAX_FILE_PATH),
        ("byRes", c_byte * 128),
    ]


class NET_DVR_INIT_CHECK_MODULE_COM(ctypes.Structure):
    _fields_ = [
        ("byEnable", c_byte),
        ("byRes", c_byte * 255),
    ]


class HikvisionSdk:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.sdk_dir = Path(os.environ.get("OC_HIKVISION_SDK_DIR") or (self.base_dir / "tools" / "hikvision"))
        self.library_name = "HCNetSDK.dll" if sys.platform.startswith("win") else "libhcnetsdk.so"
        self.dll_path = self._find_library_path()
        self.dll = None
        self.load_error = ""
        self.initialized = False
        self._exception_callback = None
        self._sessions = {}
        self._user_to_key = {}
        self._session_events = {}

    def _library_candidates(self):
        if sys.platform.startswith("win"):
            return [
                self.sdk_dir / "windows" / self.library_name,
                self.sdk_dir / self.library_name,
            ]
        return [
            self.sdk_dir / "linux" / "lib" / self.library_name,
            self.sdk_dir / "linux" / self.library_name,
            self.sdk_dir / "lib" / self.library_name,
            self.sdk_dir / self.library_name,
        ]

    def _find_library_path(self):
        for candidate in self._library_candidates():
            if candidate.exists():
                return candidate
        if not sys.platform.startswith("win"):
            matches = sorted(self.sdk_dir.glob("**/libhcnetsdk.so*"))
            if matches:
                return matches[0]
        return self._library_candidates()[0]

    def _sdk_runtime_dir(self):
        return self.dll_path.parent if self.dll_path.exists() else self.sdk_dir

    def available(self):
        self.dll_path = self._find_library_path()
        return self.dll_path.exists()

    def status(self):
        if self.initialized:
            return {"available": True, "loaded": True, "path": str(self.dll_path)}
        if self.available():
            return {"available": True, "loaded": False, "path": str(self.dll_path), "error": self.load_error}
        return {
            "available": False,
            "loaded": False,
            "path": str(self.dll_path),
            "error": f"{self.library_name} not found. Put Hikvision Device Network SDK files in tools/hikvision.",
        }

    def _load(self):
        if self.initialized:
            return True
        if not self.available():
            self.load_error = f"{self.library_name} not found"
            return False
        try:
            if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(self.sdk_dir))
            if sys.platform.startswith("win"):
                self.dll = ctypes.WinDLL(str(self.dll_path))
            else:
                lib_dir = self.dll_path.parent
                os.environ["LD_LIBRARY_PATH"] = f"{lib_dir}:{os.environ.get('LD_LIBRARY_PATH', '')}"
                load_mode = getattr(ctypes, "RTLD_GLOBAL", 0)
                for dependency in sorted(lib_dir.glob("*.so*")):
                    if dependency == self.dll_path:
                        continue
                    try:
                        ctypes.CDLL(str(dependency), mode=load_mode)
                    except OSError:
                        pass
                com_dir = lib_dir / "HCNetSDKCom"
                if com_dir.exists():
                    for dependency in sorted(com_dir.glob("*.so*")):
                        try:
                            ctypes.CDLL(str(dependency), mode=load_mode)
                        except OSError:
                            pass
                self.dll = ctypes.CDLL(str(self.dll_path), mode=load_mode)
            self.dll.NET_DVR_Init.restype = c_int
            self.dll.NET_DVR_Cleanup.restype = c_int
            self.dll.NET_DVR_GetLastError.restype = c_int
            self.dll.NET_DVR_SetSDKInitCfg.argtypes = [c_int, c_void_p]
            self.dll.NET_DVR_SetSDKInitCfg.restype = c_int
            self.dll.NET_DVR_Login_V30.argtypes = [c_char_p, c_ushort, c_char_p, c_char_p, c_void_p]
            self.dll.NET_DVR_Login_V30.restype = c_long
            self.dll.NET_DVR_Logout.argtypes = [c_long]
            self.dll.NET_DVR_Logout.restype = c_int
            self.dll.NET_DVR_GetDVRConfig.argtypes = [c_long, c_uint, c_long, c_void_p, c_uint, POINTER(c_uint)]
            self.dll.NET_DVR_GetDVRConfig.restype = c_int
            self.dll.NET_DVR_SetDVRConfig.argtypes = [c_long, c_uint, c_long, c_void_p, c_uint]
            self.dll.NET_DVR_SetDVRConfig.restype = c_int
            self.dll.NET_DVR_GetIPCProtoList.argtypes = [c_long, c_void_p]
            self.dll.NET_DVR_GetIPCProtoList.restype = c_int
            if hasattr(self.dll, "NET_DVR_SetConnectTime"):
                self.dll.NET_DVR_SetConnectTime.argtypes = [c_int, c_int]
                self.dll.NET_DVR_SetConnectTime(3000, 1)
            if hasattr(self.dll, "NET_DVR_SetReconnect"):
                self.dll.NET_DVR_SetReconnect.argtypes = [c_int, c_int]
                self.dll.NET_DVR_SetReconnect(30000, 1)
            self._set_sdk_paths()
            if not self.dll.NET_DVR_Init():
                code = self.dll.NET_DVR_GetLastError()
                self.load_error = self.error_message(code)
                return False
            self._setup_exception_callback()
            self.initialized = True
            self.load_error = ""
            return True
        except Exception as exc:
            self.load_error = str(exc)
            self.dll = None
            self.initialized = False
            return False

    def _setup_exception_callback(self):
        if not hasattr(self.dll, "NET_DVR_SetExceptionCallBack_V30") or self._exception_callback:
            return
        callback_type = ctypes.WINFUNCTYPE(None, c_uint, c_long, c_long, c_void_p) if sys.platform.startswith("win") else ctypes.CFUNCTYPE(None, c_uint, c_long, c_long, c_void_p)

        def on_exception(event_type, user_id, handle, _user):
            key = self._user_to_key.get(int(user_id))
            if not key:
                return
            event_names = {
                0x8000: "network_exception",
                0x8003: "preview_exception",
                0x8005: "preview_reconnect",
                0x8015: "preview_reconnect_success",
                0x8017: "network_resume",
            }
            status = "online" if int(event_type) in {0x8005, 0x8015, 0x8017} else "offline"
            self._session_events[key] = {
                "status": status,
                "event_type": int(event_type),
                "event_name": event_names.get(int(event_type), f"sdk_event_{int(event_type)}"),
                "user_id": int(user_id),
                "handle": int(handle),
            }

        self._exception_callback = callback_type(on_exception)
        self.dll.NET_DVR_SetExceptionCallBack_V30.argtypes = [c_uint, c_void_p, callback_type, c_void_p]
        self.dll.NET_DVR_SetExceptionCallBack_V30.restype = c_int
        self.dll.NET_DVR_SetExceptionCallBack_V30(0, None, self._exception_callback, None)

    def _set_sdk_paths(self):
        runtime_dir = self._sdk_runtime_dir()
        sdk_path = str(runtime_dir.resolve()) + os.sep
        path_cfg = NET_DVR_LOCAL_SDK_PATH()
        path_cfg.sPath = sdk_path.encode("utf-8")[: NET_SDK_MAX_FILE_PATH - 1]
        self.dll.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, byref(path_cfg))
        crypto_file = runtime_dir / ("libcrypto-1_1-x64.dll" if sys.platform.startswith("win") else "libcrypto.so.1.1")
        ssl_file = runtime_dir / ("libssl-1_1-x64.dll" if sys.platform.startswith("win") else "libssl.so.1.1")
        crypto = str(crypto_file.resolve()).encode("utf-8")
        ssl_path = str(ssl_file.resolve()).encode("utf-8")
        if crypto_file.exists():
            self.dll.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, c_char_p(crypto))
        if ssl_file.exists():
            self.dll.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, c_char_p(ssl_path))
        check = NET_DVR_INIT_CHECK_MODULE_COM()
        check.byEnable = 1
        self.dll.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_TYPE_CHECK_MODULE_COM, byref(check))

    def _login(self, host, port, username, password):
        device_info = NET_DVR_DEVICEINFO_V30()
        user_id = self.dll.NET_DVR_Login_V30(
            str(host).encode("utf-8"),
            int(port),
            str(username).encode("utf-8"),
            str(password).encode("utf-8"),
            byref(device_info),
        )
        return user_id, device_info

    def error_message(self, code):
        return HIKVISION_ERROR_MESSAGES.get(int(code), f"Hikvision SDK error {code}")

    def login_check(self, host, port, username, password):
        if not host:
            return {"status": "not_configured", "message": "NVR IP is required", "sdk_error_code": None}
        if not username:
            return {"status": "not_configured", "message": "NVR username is required", "sdk_error_code": None}
        if not password:
            return {"status": "missing_password", "message": "NVR password is required", "sdk_error_code": None}
        if not self._load():
            return {
                "status": "sdk_missing" if not self.available() else "sdk_load_failed",
                "message": self.status()["error"],
                "sdk_error_code": None,
            }
        user_id, device_info = self._login(host, port, username, password)
        if user_id < 0:
            code = self.dll.NET_DVR_GetLastError()
            message = self.error_message(code)
            status = "auth_failed" if int(code) in {1, 47, 153, 154, 155} else "offline"
            return {"status": status, "message": message, "sdk_error_code": int(code)}
        self.dll.NET_DVR_Logout(user_id)
        serial = bytes(device_info.sSerialNumber).split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        return {
            "status": "online",
            "message": "Hikvision SDK login successful",
            "sdk_error_code": 0,
            "device_info": {
                "serial": serial,
                "device_type": int(device_info.wDevType),
                "analog_channels": int(device_info.byChanNum),
                "ip_channels": int(device_info.byIPChanNum),
                "start_channel": int(device_info.byStartChan),
                "start_ip_channel": int(device_info.byStartDChan),
                "disk_count": int(device_info.byDiskNum),
            },
        }

    def ensure_monitor(self, key, host, port, username, password):
        if not key or not host or not username or not password:
            return {"status": "not_configured", "message": "NVR monitor requires IP, username, and password"}
        if not self._load():
            return {
                "status": "sdk_missing" if not self.available() else "sdk_load_failed",
                "message": self.status()["error"],
                "sdk_error_code": None,
            }
        desired = {
            "host": str(host),
            "port": int(port),
            "username": str(username),
            "password": str(password),
        }
        session = self._sessions.get(str(key))
        if session and all(session.get(k) == desired[k] for k in desired):
            event = self._session_events.get(str(key)) or {}
            return {
                "status": event.get("status") or "online",
                "message": event.get("event_name") or "SDK monitor active",
                "sdk_error_code": 0,
                "user_id": session.get("user_id"),
                "event": event,
            }
        self.close_monitor(key)
        user_id, device_info = self._login(host, port, username, password)
        if user_id < 0:
            code = self.dll.NET_DVR_GetLastError()
            message = self.error_message(code)
            status = "auth_failed" if int(code) in {1, 47, 153, 154, 155} else "offline"
            return {"status": status, "message": message, "sdk_error_code": int(code)}
        serial = bytes(device_info.sSerialNumber).split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        self._sessions[str(key)] = {**desired, "user_id": int(user_id)}
        self._user_to_key[int(user_id)] = str(key)
        self._session_events[str(key)] = {"status": "online", "event_name": "SDK monitor active", "user_id": int(user_id)}
        return {
            "status": "online",
            "message": "SDK monitor active",
            "sdk_error_code": 0,
            "user_id": int(user_id),
            "device_info": {
                "serial": serial,
                "device_type": int(device_info.wDevType),
                "analog_channels": int(device_info.byChanNum),
                "ip_channels": int(device_info.byIPChanNum),
                "start_channel": int(device_info.byStartChan),
                "start_ip_channel": int(device_info.byStartDChan),
                "disk_count": int(device_info.byDiskNum),
            },
        }

    def monitor_event(self, key):
        return dict(self._session_events.get(str(key)) or {})

    def close_monitor(self, key):
        session = self._sessions.pop(str(key), None)
        if not session or not self.dll:
            self._session_events.pop(str(key), None)
            return
        user_id = int(session.get("user_id") or -1)
        self._user_to_key.pop(user_id, None)
        self._session_events.pop(str(key), None)
        if user_id >= 0:
            self.dll.NET_DVR_Logout(user_id)

    def _set_custom_rtsp_protocol_for_user(self, user_id, protocol_index, rtsp_port, stream_path, name=None):
        cfg = NET_DVR_CUSTOM_PROTOCAL()
        cfg.dwSize = ctypes.sizeof(NET_DVR_CUSTOM_PROTOCAL)
        cfg.dwEnabled = 1
        cfg.dwEnableSubStream = 0
        cfg.byMainProType = 1
        cfg.byMainTransType = 2
        cfg.wMainPort = int(rtsp_port)
        proto_name = (name or f"OC_CH{int(protocol_index):02d}")[: DESC_LEN - 1].encode("ascii", errors="ignore")
        main_path = str(stream_path or "").encode("utf-8")[: MAX_PRO_PATH - 1]
        cfg.sProtocalName = proto_name
        cfg.sMainPath = main_path
        if not self.dll.NET_DVR_SetDVRConfig(user_id, NET_DVR_SET_CUSTOM_PRO_CFG, int(protocol_index), byref(cfg), ctypes.sizeof(cfg)):
            code = self.dll.NET_DVR_GetLastError()
            return {"status": "sync_failed", "message": self.error_message(code), "sdk_error_code": int(code)}
        return {
            "status": "custom_protocol_synced",
            "message": f"Custom RTSP protocol {protocol_index} updated",
            "sdk_error_code": 0,
            "protocol_index": int(protocol_index),
            "rtsp_port": int(rtsp_port),
            "stream_path": str(stream_path or ""),
        }

    def set_custom_rtsp_protocol(self, host, port, username, password, protocol_index, rtsp_port, stream_path, name=None):
        if not self._load():
            return {
                "status": "sdk_missing" if not self.available() else "sdk_load_failed",
                "message": self.status()["error"],
                "sdk_error_code": None,
            }
        if not password:
            return {"status": "missing_password", "message": "NVR password is required", "sdk_error_code": None}
        user_id, _device_info = self._login(host, port, username, password)
        if user_id < 0:
            code = self.dll.NET_DVR_GetLastError()
            message = self.error_message(code)
            status = "auth_failed" if int(code) in {1, 47, 153, 154, 155} else "offline"
            return {"status": status, "message": message, "sdk_error_code": int(code)}
        try:
            return self._set_custom_rtsp_protocol_for_user(user_id, protocol_index, rtsp_port, stream_path, name=name)
        finally:
            self.dll.NET_DVR_Logout(user_id)

    def bind_rtsp_url_channel(self, host, port, username, password, channel, rtsp_url):
        if not self._load():
            return {
                "status": "sdk_missing" if not self.available() else "sdk_load_failed",
                "message": self.status()["error"],
                "sdk_error_code": None,
            }
        if not password:
            return {"status": "missing_password", "message": "NVR password is required", "sdk_error_code": None}
        if not rtsp_url:
            return {"status": "missing_rtsp_url", "message": "RTSP URL is required", "sdk_error_code": None}
        if len(str(rtsp_url).encode("utf-8")) >= URL_LEN:
            return {
                "status": "url_too_long",
                "message": f"RTSP URL is too long for Hikvision URL channel binding. Max {URL_LEN - 1} bytes.",
                "sdk_error_code": None,
            }
        user_id, _device_info = self._login(host, port, username, password)
        if user_id < 0:
            code = self.dll.NET_DVR_GetLastError()
            message = self.error_message(code)
            status = "auth_failed" if int(code) in {1, 47, 153, 154, 155} else "offline"
            return {"status": status, "message": message, "sdk_error_code": int(code)}
        try:
            cfg = NET_DVR_IPPARACFG_V40()
            cfg.dwSize = ctypes.sizeof(NET_DVR_IPPARACFG_V40)
            returned = c_uint(0)
            if not self.dll.NET_DVR_GetDVRConfig(
                user_id,
                NET_DVR_GET_IPPARACFG_V40,
                0,
                byref(cfg),
                ctypes.sizeof(cfg),
                byref(returned),
            ):
                code = self.dll.NET_DVR_GetLastError()
                return {"status": "channel_get_failed", "message": self.error_message(code), "sdk_error_code": int(code)}

            channel_index = max(0, min(MAX_CHANNUM_V30 - 1, int(channel) - 1))
            stream = cfg.struStreamMode[channel_index]
            ctypes.memset(byref(stream.uGetStream), 0, ctypes.sizeof(NET_DVR_GET_STREAM_UNION))
            stream.byGetStreamType = NET_SDK_STREAM_MEDIA_URL
            stream.uGetStream.struStreamUrl.byEnable = 1
            stream.uGetStream.struStreamUrl.byTransPortocol = 0
            stream.uGetStream.struStreamUrl.wIPID = channel_index + 1
            stream.uGetStream.struStreamUrl.byChannel = int(channel)
            _set_byte_array(stream.uGetStream.struStreamUrl.strURL, rtsp_url, URL_LEN)

            if not self.dll.NET_DVR_SetDVRConfig(
                user_id,
                NET_DVR_SET_IPPARACFG_V40,
                0,
                byref(cfg),
                ctypes.sizeof(cfg),
            ):
                code = self.dll.NET_DVR_GetLastError()
                return {"status": "channel_bind_failed", "message": self.error_message(code), "sdk_error_code": int(code)}
            return {
                "status": "channel_bound",
                "message": f"NVR channel {int(channel)} bound to DJI RTSP URL",
                "sdk_error_code": 0,
                "channel": int(channel),
                "rtsp_url": str(rtsp_url),
                "returned_bytes": int(returned.value),
            }
        finally:
            self.dll.NET_DVR_Logout(user_id)

    def _ipc_protocols(self, user_id):
        proto_list = NET_DVR_IPC_PROTO_LIST()
        proto_list.dwSize = ctypes.sizeof(NET_DVR_IPC_PROTO_LIST)
        if not self.dll.NET_DVR_GetIPCProtoList(user_id, byref(proto_list)):
            code = self.dll.NET_DVR_GetLastError()
            return None, {"status": "protocol_list_failed", "message": self.error_message(code), "sdk_error_code": int(code)}
        protocols = []
        for index in range(min(int(proto_list.dwProtoNum), IPC_PROTOCOL_NUM)):
            item = proto_list.struProto[index]
            protocols.append({"type": int(item.dwType), "name": _byte_array_text(item.byDescribe)})
        return protocols, None

    def _find_protocol_type(self, user_id, protocol_name, protocol_index):
        protocols, error = self._ipc_protocols(user_id)
        if error:
            return None, protocols, error
        expected = str(protocol_name or "").strip().lower()
        for item in protocols:
            if item["name"].strip().lower() == expected:
                return item["type"], protocols, None
        fallback_names = {f"custom {int(protocol_index)}", f"oc_ch{int(protocol_index):02d}"}
        for item in protocols:
            if item["name"].strip().lower() in fallback_names:
                return item["type"], protocols, None
        return None, protocols, {
            "status": "protocol_not_found",
            "message": f"Custom protocol '{protocol_name}' not found in NVR protocol list",
            "sdk_error_code": None,
        }

    def _clear_ip_channel_slot_in_cfg(self, cfg, channel_index):
        device = cfg.struIPDevInfo[channel_index]
        device.byEnable = 0
        device.byProType = 0
        device.byEnableQuickAdd = 0
        device.byCameraType = 0
        _set_byte_array(device.sUserName, "", NAME_LEN)
        _set_byte_array(device.sPassword, "", PASSWD_LEN)
        _set_byte_array(device.byDomain, "", MAX_DOMAIN_NAME)
        device.struIP.sIpV4 = b""
        for i in range(len(device.struIP.byIPv6)):
            device.struIP.byIPv6[i] = 0
        device.wDVRPort = 0
        _set_byte_array(device.szDeviceID, "", DEV_ID_LEN)
        device.byEnableTiming = 0
        device.byCertificateValidation = 0

        stream = cfg.struStreamMode[channel_index]
        stream.byGetStreamType = 0
        ctypes.memset(byref(stream.uGetStream), 0, ctypes.sizeof(NET_DVR_GET_STREAM_UNION))

    def _write_ippara_cfg(self, user_id, cfg, fail_status):
        if not self.dll.NET_DVR_SetDVRConfig(
            user_id,
            NET_DVR_SET_IPPARACFG_V40,
            0,
            byref(cfg),
            ctypes.sizeof(cfg),
        ):
            code = self.dll.NET_DVR_GetLastError()
            return {"status": fail_status, "message": self.error_message(code), "sdk_error_code": int(code)}
        return None

    def bind_custom_protocol_ip_channel(
        self,
        host,
        port,
        username,
        password,
        channel,
        camera_host,
        camera_port,
        camera_username,
        camera_password,
        protocol_type,
        clear_first=True,
        camera_name="",
    ):
        cfg = NET_DVR_IPPARACFG_V40()
        cfg.dwSize = ctypes.sizeof(NET_DVR_IPPARACFG_V40)
        returned = c_uint(0)
        if not self.dll.NET_DVR_GetDVRConfig(
            self._active_user_id,
            NET_DVR_GET_IPPARACFG_V40,
            0,
            byref(cfg),
            ctypes.sizeof(cfg),
            byref(returned),
        ):
            code = self.dll.NET_DVR_GetLastError()
            return {"status": "channel_get_failed", "message": self.error_message(code), "sdk_error_code": int(code)}

        channel_index = max(0, min(MAX_CHANNUM_V30 - 1, int(channel) - 1))
        device_index = channel_index

        # Some Hikvision NVR firmware rejects changing an enabled IP-camera slot
        # directly and returns SDK error 29. Clear only this target slot first,
        # commit it, then read back and write the new custom-protocol camera.
        if clear_first:
            self._clear_ip_channel_slot_in_cfg(cfg, channel_index)
            clear_error = self._write_ippara_cfg(self._active_user_id, cfg, "channel_clear_failed")
            if clear_error:
                clear_error["channel"] = int(channel)
                return clear_error
            time.sleep(0.35)
            cfg = NET_DVR_IPPARACFG_V40()
            cfg.dwSize = ctypes.sizeof(NET_DVR_IPPARACFG_V40)
            returned = c_uint(0)
            if not self.dll.NET_DVR_GetDVRConfig(
                self._active_user_id,
                NET_DVR_GET_IPPARACFG_V40,
                0,
                byref(cfg),
                ctypes.sizeof(cfg),
                byref(returned),
            ):
                code = self.dll.NET_DVR_GetLastError()
                return {"status": "channel_get_after_clear_failed", "message": self.error_message(code), "sdk_error_code": int(code)}

        device = cfg.struIPDevInfo[device_index]
        device.byEnable = 1
        device.byProType = int(protocol_type) & 0xFF
        device.byEnableQuickAdd = 0
        device.byCameraType = 0
        _set_byte_array(device.sUserName, camera_username, NAME_LEN)
        _set_byte_array(device.sPassword, camera_password, PASSWD_LEN)
        _set_byte_array(device.byDomain, "", MAX_DOMAIN_NAME)
        device.struIP.sIpV4 = b""
        for i in range(len(device.struIP.byIPv6)):
            device.struIP.byIPv6[i] = 0
        if _is_ipv4(camera_host):
            device.struIP.sIpV4 = str(camera_host).encode("ascii")[:15]
        else:
            _set_byte_array(device.byDomain, camera_host, MAX_DOMAIN_NAME)
        device.wDVRPort = int(camera_port)
        # Hikvision shows the IP camera display name from szDeviceID on many NVR models.
        # Keep protocol name separate; use converter_name only for the NVR Device Name.
        _set_byte_array(device.szDeviceID, camera_name or "", DEV_ID_LEN)
        device.byEnableTiming = 0
        device.byCertificateValidation = 0

        stream = cfg.struStreamMode[channel_index]
        stream.byGetStreamType = NET_SDK_IP_DEVICE
        ctypes.memset(byref(stream.uGetStream), 0, ctypes.sizeof(NET_DVR_GET_STREAM_UNION))
        stream.uGetStream.struChanInfo.byEnable = 1
        stream.uGetStream.struChanInfo.byIPID = (device_index + 1) & 0xFF
        stream.uGetStream.struChanInfo.byIPIDHigh = ((device_index + 1) >> 8) & 0xFF
        stream.uGetStream.struChanInfo.byChannel = 1
        stream.uGetStream.struChanInfo.byTransProtocol = 0
        stream.uGetStream.struChanInfo.byGetStream = 0

        write_error = self._write_ippara_cfg(self._active_user_id, cfg, "channel_bind_failed")
        if write_error:
            write_error.update({
                "channel": int(channel),
                "camera_host": str(camera_host or ""),
                "camera_port": int(camera_port),
                "protocol_type": int(protocol_type),
                "cleared_before_bind": bool(clear_first),
            })
            return write_error
        return {
            "status": "ip_channel_bound",
            "message": f"NVR channel {int(channel)} added/updated with DJI camera IP and custom RTSP protocol",
            "sdk_error_code": 0,
            "channel": int(channel),
            "protocol_type": int(protocol_type),
            "camera_host": str(camera_host),
            "camera_port": int(camera_port),
            "camera_name": str(camera_name or ""),
            "returned_bytes": int(returned.value),
            "cleared_before_bind": bool(clear_first),
        }

    def clear_ip_channels(self, host, port, username, password, channels):
        if not self._load():
            return {
                "status": "sdk_missing" if not self.available() else "sdk_load_failed",
                "message": self.status()["error"],
                "sdk_error_code": None,
            }
        if not password:
            return {"status": "missing_password", "message": "NVR password is required", "sdk_error_code": None}
        requested = sorted({int(ch) for ch in channels if str(ch).strip().isdigit() and int(ch) > 0})
        if not requested:
            return {"status": "nothing_to_clear", "message": "No AERO SYNC channels selected", "sdk_error_code": 0, "cleared": []}
        user_id, _device_info = self._login(host, port, username, password)
        if user_id < 0:
            code = self.dll.NET_DVR_GetLastError()
            message = self.error_message(code)
            status = "auth_failed" if int(code) in {1, 47, 153, 154, 155} else "offline"
            return {"status": status, "message": message, "sdk_error_code": int(code), "cleared": []}
        self._active_user_id = user_id
        try:
            cfg = NET_DVR_IPPARACFG_V40()
            cfg.dwSize = ctypes.sizeof(NET_DVR_IPPARACFG_V40)
            returned = c_uint(0)
            if not self.dll.NET_DVR_GetDVRConfig(
                user_id,
                NET_DVR_GET_IPPARACFG_V40,
                0,
                byref(cfg),
                ctypes.sizeof(cfg),
                byref(returned),
            ):
                code = self.dll.NET_DVR_GetLastError()
                return {"status": "channel_get_failed", "message": self.error_message(code), "sdk_error_code": int(code), "cleared": []}

            cleared = []
            for channel in requested:
                channel_index = channel - 1
                if channel_index < 0 or channel_index >= MAX_CHANNUM_V30:
                    continue
                device = cfg.struIPDevInfo[channel_index]
                device.byEnable = 0
                device.byProType = 0
                device.byEnableQuickAdd = 0
                device.byCameraType = 0
                _set_byte_array(device.sUserName, "", NAME_LEN)
                _set_byte_array(device.sPassword, "", PASSWD_LEN)
                _set_byte_array(device.byDomain, "", MAX_DOMAIN_NAME)
                device.struIP.sIpV4 = b""
                device.wDVRPort = 0
                device.byEnableTiming = 0
                device.byCertificateValidation = 0

                stream = cfg.struStreamMode[channel_index]
                stream.byGetStreamType = 0
                ctypes.memset(byref(stream.uGetStream), 0, ctypes.sizeof(NET_DVR_GET_STREAM_UNION))
                stream.uGetStream.struChanInfo.byEnable = 0
                stream.uGetStream.struChanInfo.byIPID = 0
                stream.uGetStream.struChanInfo.byIPIDHigh = 0
                stream.uGetStream.struChanInfo.byChannel = 0
                stream.uGetStream.struChanInfo.byTransProtocol = 0
                stream.uGetStream.struChanInfo.byGetStream = 0
                cleared.append(channel)

            if not cleared:
                return {"status": "nothing_to_clear", "message": "No valid NVR channel numbers found", "sdk_error_code": 0, "cleared": []}

            if not self.dll.NET_DVR_SetDVRConfig(
                user_id,
                NET_DVR_SET_IPPARACFG_V40,
                0,
                byref(cfg),
                ctypes.sizeof(cfg),
            ):
                code = self.dll.NET_DVR_GetLastError()
                return {"status": "clear_failed", "message": self.error_message(code), "sdk_error_code": int(code), "cleared": []}
            return {
                "status": "cleared",
                "message": f"Cleared {len(cleared)} AERO SYNC NVR channel(s)",
                "sdk_error_code": 0,
                "cleared": cleared,
                "returned_bytes": int(returned.value),
            }
        finally:
            self.dll.NET_DVR_Logout(user_id)
            self._active_user_id = None

    def sync_dji_rtsp_channel(
        self,
        host,
        port,
        username,
        password,
        channel,
        rtsp_port,
        stream_path,
        rtsp_url,
        name=None,
        camera_username="",
        camera_password="",
        camera_name="",
    ):
        """
        Sync one DJI/Event API RTSP source to one fixed NVR channel.

        This uses Hikvision custom RTSP protocol + IP device binding so the
        channel appears in the NVR IP camera/channel list. A later event for
        the same source updates the same NVR channel because the app mapping
        reuses source_key -> nvr_channel.

        Important regression fixes:
        - no nested/double SDK login during one sync;
        - no SDK-29 success masking;
        - clear only the target IP-channel slot before rewriting it, because
          many NVRs reject direct edits to an enabled IP channel with SDK 29.
        """
        if not self._load():
            return {
                "status": "sdk_missing" if not self.available() else "sdk_load_failed",
                "message": self.status()["error"],
                "sdk_error_code": None,
            }
        if not password:
            return {"status": "missing_password", "message": "NVR password is required", "sdk_error_code": None}
        try:
            selected_channel = int(channel)
        except (TypeError, ValueError):
            return {"status": "invalid_channel", "message": "NVR channel must be a positive number", "sdk_error_code": None}
        if selected_channel < 1 or selected_channel > MAX_CHANNUM_V30:
            return {"status": "invalid_channel", "message": f"NVR channel must be between 1 and {MAX_CHANNUM_V30}", "sdk_error_code": None}
        if not rtsp_url:
            return {"status": "missing_rtsp_url", "message": "RTSP URL is required", "sdk_error_code": None}

        user_id, _device_info = self._login(host, port, username, password)
        if user_id < 0:
            code = self.dll.NET_DVR_GetLastError()
            message = self.error_message(code)
            status = "auth_failed" if int(code) in {1, 47, 153, 154, 155} else "offline"
            return {"status": status, "message": message, "sdk_error_code": int(code)}

        self._active_user_id = user_id
        try:
            protocol_slot = _custom_protocol_slot(selected_channel)
            protocol_name = (name or f"OC_CH{protocol_slot:02d}")[: DESC_LEN - 1]
            protocol_result = self._set_custom_rtsp_protocol_for_user(
                user_id,
                protocol_slot,
                rtsp_port,
                stream_path,
                name=protocol_name,
            )
            if protocol_result.get("status") != "custom_protocol_synced":
                return protocol_result

            protocol_type, protocols, protocol_error = self._find_protocol_type(user_id, protocol_name, protocol_slot)
            if protocol_error:
                protocol_error["protocols"] = protocols or []
                protocol_error["protocol_slot"] = protocol_slot
                protocol_error["protocol_name"] = protocol_name
                return protocol_error

            parts = urlsplit(rtsp_url or "")
            camera_host = parts.hostname or ""
            if not camera_host:
                return {"status": "invalid_rtsp_url", "message": "RTSP URL host is missing", "sdk_error_code": None}
            camera_username = camera_username or parts.username or ""
            camera_password = camera_password or parts.password or ""
            channel_result = self.bind_custom_protocol_ip_channel(
                host,
                port,
                username,
                password,
                selected_channel,
                camera_host,
                rtsp_port,
                camera_username,
                camera_password,
                protocol_type,
                clear_first=True,
                camera_name=camera_name,
            )
            if channel_result.get("status") == "ip_channel_bound":
                return {
                    "status": "channel_synced",
                    "message": f"{protocol_result.get('message')}; {channel_result.get('message')}",
                    "sdk_error_code": 0,
                    "protocol": protocol_result,
                    "channel": channel_result,
                    "channel_number": selected_channel,
                    "protocol_type": int(protocol_type),
                    "protocol_slot": protocol_slot,
                    "protocol_name": protocol_name,
                    "sync_mode": "custom_protocol_ip_channel",
                }
            return channel_result
        finally:
            self.dll.NET_DVR_Logout(user_id)
            self._active_user_id = None
