"""HVF (HIPS Vessel File) reader.

Parses CARIS HIPS vessel configuration XML files (.hvf) containing
sensor offsets, mounting angles, and calibration parameters.

These files define the spatial relationship between the vessel reference
point and all installed sensors (transducer, MRU, GNSS antenna, etc.).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .models import HvfFile, HvfSensorOffset


def read_hvf(filepath: str | Path) -> HvfFile:
    """Read an HVF vessel configuration file.

    Args:
        filepath: Path to .hvf file.

    Returns:
        HvfFile with sensor offsets and mounting angles.
    """
    filepath = Path(filepath)
    result = HvfFile(filepath=str(filepath))

    tree = ET.parse(filepath)
    root = tree.getroot()

    # Extract vessel shape / RP
    rp = root.find(".//RP")
    if rp is not None:
        result.sections["RP"] = dict(rp.attrib)

    # Parse all sensor types
    _parse_sensor_group(root, "DepthSensor", result)
    _parse_sensor_group(root, "NavigationSensor", result)
    _parse_sensor_group(root, "GyroSensor", result)
    _parse_sensor_group(root, "HeaveSensor", result)
    _parse_sensor_group(root, "PitchRollSensor", result)
    _parse_sensor_group(root, "SVPSensor", result)

    # Extract vessel name from filename convention
    result.vessel_name = filepath.stem

    return result


def _parse_sensor_group(root: ET.Element, group_name: str, result: HvfFile) -> None:
    """Parse a sensor group (e.g., DepthSensor) and extract offsets."""
    for group in root.iter(group_name):
        for timestamp in group.iter("TimeStamp"):
            ts_value = timestamp.get("value", "")

            # Store raw section data
            section_key = f"{group_name}_{ts_value}" if ts_value else group_name
            section_data: dict[str, str] = {"TimeStamp": ts_value}

            # Latency
            latency_el = timestamp.find("Latency")
            if latency_el is not None:
                section_data["Latency"] = latency_el.get("value", "0")

            # Sensor class
            sc_el = timestamp.find("SensorClass")
            if sc_el is not None:
                section_data["SensorClass"] = sc_el.get("value", "")

            # Transducer entries
            for transducer in timestamp.iter("Transducer"):
                t_num = transducer.get("Number", "?")
                t_model = transducer.get("Model", "Unknown")
                section_data[f"Transducer_{t_num}_Model"] = t_model

                # Manufacturer
                mfg = transducer.find("Manufacturer")
                if mfg is not None:
                    section_data[f"Transducer_{t_num}_Manufacturer"] = mfg.get("value", "")

                # Offsets (lever arms)
                offsets_el = transducer.find("Offsets")
                if offsets_el is not None:
                    x = _float(offsets_el.get("X", "0"))
                    y = _float(offsets_el.get("Y", "0"))
                    z = _float(offsets_el.get("Z", "0"))
                    lat_offset = _float(offsets_el.get("Latency", "0"))

                    section_data[f"Transducer_{t_num}_X"] = str(x)
                    section_data[f"Transducer_{t_num}_Y"] = str(y)
                    section_data[f"Transducer_{t_num}_Z"] = str(z)
                    section_data[f"Transducer_{t_num}_Latency"] = str(lat_offset)

                # Mount angles
                mount_el = transducer.find("MountAngle")
                if mount_el is not None:
                    pitch = _float(mount_el.get("Pitch", "0"))
                    roll = _float(mount_el.get("Roll", "0"))
                    azimuth = _float(mount_el.get("Azimuth", "0"))

                    section_data[f"Transducer_{t_num}_MountPitch"] = str(pitch)
                    section_data[f"Transducer_{t_num}_MountRoll"] = str(roll)
                    section_data[f"Transducer_{t_num}_MountAzimuth"] = str(azimuth)

                    # Build sensor offset object
                    sensor = HvfSensorOffset(
                        name=f"{group_name}_T{t_num}_{ts_value}",
                        x=x, y=y, z=z,
                        pitch=pitch, roll=roll, heading=azimuth,
                    )
                    result.sensors.append(sensor)

            # Navigation/Gyro/Heave sensor offsets
            nav_offsets = timestamp.find("Offsets")
            if nav_offsets is not None and not list(timestamp.iter("Transducer")):
                x = _float(nav_offsets.get("X", "0"))
                y = _float(nav_offsets.get("Y", "0"))
                z = _float(nav_offsets.get("Z", "0"))
                section_data["X"] = str(x)
                section_data["Y"] = str(y)
                section_data["Z"] = str(z)

                sensor = HvfSensorOffset(
                    name=f"{group_name}_{ts_value}",
                    x=x, y=y, z=z,
                )
                result.sensors.append(sensor)

            result.sections[section_key] = section_data


def _float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0
