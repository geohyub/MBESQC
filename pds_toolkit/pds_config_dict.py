"""PDS @tr. Key Dictionary — Maps @tr. INI keys to human-readable descriptions.

Built from reverse-engineering 5 PDS files across 5 different projects.
Value format: type_id,actual_value[,sub_values...]
  type_id meanings:
    1    = basic parameter
    3    = reference/inherited
    17   = optional/advanced
    513  = version-dependent
    1027 = device-linked
    4097 = computation-specific override
    5123 = high-priority override
    8193 = calibration measurement (read-only from auto-cal)
"""

# QC-Critical keys: these are the ones that matter for data validation
QC_CRITICAL = {
    # ── Motion Application ────────────────────────────────────
    'ApplyRoll':       'Roll correction ON/OFF (1=ON, 0=OFF)',
    'ApplyPitch':      'Pitch correction ON/OFF',
    'ApplyHeave':      'Heave correction ON/OFF',
    'ApplyBearing':    'Bearing correction ON/OFF',
    'ApplyVru':        'VRU (Motion Reference Unit) ON/OFF',
    'ApplyHdg':        'Heading correction ON/OFF',
    'ApplySvp':        'SVP ray-tracing ON/OFF',

    # ── Calibration Values ────────────────────────────────────
    'StaticRoll':      'Applied roll calibration (degrees, from patch test)',
    'StaticPitch':     'Applied pitch calibration (degrees, from patch test)',
    'AutoStaticComp':  'Auto static compensation ON/OFF',
    'HeaveFactor':     'Heave scale factor (should be 1.0)',
    'RollCor':         'MRU roll mounting offset (degrees)',
    'PitchCor':        'MRU pitch mounting offset (degrees)',
    'HdgCor':          'Heading correction (degrees, includes mag declination)',
    'HeaveCor':        'Heave correction value (meters)',
    'HeaveDelay':      'Heave latency correction (seconds)',

    # ── SVP Settings ──────────────────────────────────────────
    'SvpFileName':     'Active SVP filename',
    'SvpFileTime':     'SVP file timestamp',
    'SvpFileCrc':      'SVP file checksum',
    'UseSSV':          'Use Surface Sound Velocity ON/OFF',
    'SDEVSVP':         'SVP uncertainty (m/s)',
    'SDEVSPSS':        'Surface sound speed uncertainty (m/s)',

    # ── Data Source IDs ───────────────────────────────────────
    'RollDataId':      'Roll data source device ID',
    'PitchDataId':     'Pitch data source device ID',
    'HeaveDataId':     'Heave data source device ID',
    'HdgDataId':       'Heading data source device ID',
    'MbeamDataId':     'Multibeam data source device ID',
    'SealevelDataId':  'Sealevel/tide data source ID',
    'VruDataId':       'VRU data source device ID',
    'PosDataId':       'Position data source device ID',
    'RefposDataId':    'Reference position data source ID',

    # ── Beam Filters ──────────────────────────────────────────
    'QualityFilter':    'Quality filter ON/OFF',
    'FilterQuality':    'Quality threshold (1=best, 4=worst accepted)',
    'DepthFilter':      'Depth filter ON/OFF',
    'FilterMinDepth':   'Min depth filter (meters)',
    'FilterMaxDepth':   'Max depth filter (meters)',
    'RangeFilter':      'Range filter ON/OFF',
    'FilterMinRange':   'Min range (meters)',
    'FilterMaxRange':   'Max range (meters)',
    'NadirFilter':      'Nadir rejection filter ON/OFF',
    'AngleFilter':      'Beam angle filter ON/OFF',
    'FilterMinAngle':   'Min beam angle for filtering (degrees)',
    'BeamRejectFilter': 'Beam reject filter ON/OFF',
    'MaxBeamReject':    'Max % beams to reject per ping',
    'SlopeFilter':      'Slope filter ON/OFF',
    'FilterSlopeAngle': 'Max slope angle (degrees)',
    'DetectionFilter':  'Detection type filter ON/OFF',
    'SmartFilter':      'Smart filter ON/OFF',
    'IntensityFilter':  'Intensity filter ON/OFF',
    'MultiDetectFilter': 'Multi-detect filter ON/OFF',
    'FlyingObjectsFilter': 'Flying objects filter ON/OFF',

    # ── Sonar Configuration ───────────────────────────────────
    '7kCoverageAngle':  'Sonar coverage angle (degrees)',
    '7kCustomBeams':    'Number of beams configured',
    '7kBeamModeName':   'Beam spacing mode (Equi-Angle/Equi-Distant)',
    '7kMaxRangeGate':   'Max range gate (meters)',
    '7kMinRangeGate':   'Min range gate (meters)',
    '7kBeamWidthX':     'Beam width cross-track (degrees)',
    '7kBeamWidthZ':     'Beam width along-track (degrees)',

    # ── Sensor Uncertainty ────────────────────────────────────
    'SDEVRoll':         'Roll sensor std dev (degrees)',
    'SDEVPitch':        'Pitch sensor std dev (degrees)',
    'SDEVRollPitch':    'Roll/Pitch combined std dev (degrees)',
    'SDEVDynHeave':     'Dynamic heave std dev (meters)',
    'SDEVFixHeave':     'Fixed heave std dev (meters)',
    'SDEVGPSOffset':    'GPS position std dev (meters)',
    'SDEVHeading':      'Heading sensor std dev (degrees)',
    'GYROSdev':         'Gyro std dev (degrees)',
    'GPSLatencySdev':   'GPS latency std dev (seconds)',
    'SDEVVRULattency':  'VRU latency std dev (seconds)',

    # ── Time Settings ─────────────────────────────────────────
    'TimeDelay':        'Sensor time delay (seconds)',
    'TimeStampMode':    'Timestamp mode (1=internal, 2=external PPS)',
    'MaxPosAge':        'Max position age before gap (seconds)',
    'MaxDeadReckon':    'Max dead reckoning time (seconds)',
    'VruMaxAge':        'Max VRU data age before gap (ms)',
    'HdgMaxAge':        'Max heading data age before gap (ms)',
    'MaxGapTime':       'Max gap time for gap detection (seconds)',
    'GapCheckEnable':   'Gap detection ON/OFF',

    # ── IHO / TPU ─────────────────────────────────────────────
    'IHOError':         'IHO error computation ON/OFF',
    'IHOErrorStandard': 'IHO standard (1=Order 1, 2=Special, etc)',
    'CustomError':      'Custom error value ON/OFF',
    'CustomErrorValue': 'Custom vertical error (meters)',

    # ── Backscatter ───────────────────────────────────────────
    'NormalizedBackscatter': 'Normalized backscatter ON/OFF',
    'LambersCorrection':    'Lamberts correction ON/OFF',
    'ReduceData':           'Reduce backscatter data ON/OFF',
    'MaxSnippetSize':       'Max snippet window size (samples)',
}


def extract_value(raw: str) -> str:
    """Extract actual value from PDS @tr. value format 'type_id,value'."""
    parts = raw.split(',', 1)
    return parts[1] if len(parts) > 1 else parts[0]


def extract_typed(raw: str) -> tuple:
    """Return (type_id: int, actual_value: str) from raw value."""
    parts = raw.split(',', 1)
    if len(parts) > 1:
        try:
            return int(parts[0]), parts[1]
        except ValueError:
            return 0, raw
    return 0, raw


def describe_key(key_suffix: str) -> str:
    """Get description for a @tr. key suffix."""
    return QC_CRITICAL.get(key_suffix, f'Unknown key: {key_suffix}')
