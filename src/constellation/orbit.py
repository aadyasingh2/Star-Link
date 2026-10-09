import numpy as np
from .constants import EARTH_RADIUS_KM, MU_EARTH_KM3_S2, EARTH_OMEGA_RAD_S

class Constellation:
    def __init__(self, config):
        self.planes = config['constellation']['planes']
        self.sats_per_plane = config['constellation']['sats_per_plane']
        self.phasing = config['constellation']['phasing']
        self.altitude_km = config['constellation']['altitude_km']
        self.inclination_deg = config['constellation']['inclination_deg']
        
        self.n_sats = self.planes * self.sats_per_plane
        self.a = EARTH_RADIUS_KM + self.altitude_km
        
        # Kepler's third law for orbital period
        self.mean_motion = np.sqrt(MU_EARTH_KM3_S2 / (self.a ** 3))
        self.period_s = 2 * np.pi / self.mean_motion
        
        self.inc_rad = np.radians(self.inclination_deg)
        
        # Pre-calculate orbital elements for each satellite
        self.raan = np.zeros(self.n_sats)
        self.mean_anomaly_0 = np.zeros(self.n_sats)
        
        for p in range(self.planes):
            for s in range(self.sats_per_plane):
                idx = p * self.sats_per_plane + s
                # RAAN spread evenly over 360 degrees
                self.raan[idx] = np.radians(p * 360.0 / self.planes)
                # Mean anomaly incorporating phasing factor F
                anomaly_deg = (s * 360.0 / self.sats_per_plane) + (p * self.phasing * 360.0 / self.n_sats)
                self.mean_anomaly_0[idx] = np.radians(anomaly_deg)

    def get_ecef_positions(self, t):
        """Returns (N, 3) array of ECEF positions at time t in seconds."""
        # Current mean anomaly (for circular orbit: true anomaly = mean anomaly)
        v = self.mean_anomaly_0 + self.mean_motion * t
        
        # Position in orbital plane
        x_prime = self.a * np.cos(v)
        y_prime = self.a * np.sin(v)
        
        # Rotate by inclination and RAAN to get ECI coordinates
        cos_i = np.cos(self.inc_rad)
        sin_i = np.sin(self.inc_rad)
        cos_o = np.cos(self.raan)
        sin_o = np.sin(self.raan)
        
        x_eci = x_prime * cos_o - y_prime * cos_i * sin_o
        y_eci = x_prime * sin_o + y_prime * cos_i * cos_o
        z_eci = y_prime * sin_i
        
        eci = np.stack([x_eci, y_eci, z_eci], axis=-1)
        
        # Convert ECI to ECEF by rotating around Z axis by GMST (Earth rotation)
        gmst = EARTH_OMEGA_RAD_S * t
        cos_g = np.cos(gmst)
        sin_g = np.sin(gmst)
        
        x_ecef = eci[:, 0] * cos_g + eci[:, 1] * sin_g
        y_ecef = -eci[:, 0] * sin_g + eci[:, 1] * cos_g
        z_ecef = eci[:, 2]
        
        return np.stack([x_ecef, y_ecef, z_ecef], axis=-1)
