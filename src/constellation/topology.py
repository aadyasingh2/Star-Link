import numpy as np
import networkx as nx
from .constants import EARTH_RADIUS_KM, ATMOSPHERE_MARGIN_KM, SPEED_OF_LIGHT_KM_S

def has_los(pos1, pos2):
    """
    Vectorized Line of Sight check. 
    pos1, pos2 are (N, 3) arrays of ECEF positions.
    Returns boolean array of shape (N,) where True means LOS is clear.
    """
    v = pos2 - pos1
    v_norm_sq = np.sum(v * v, axis=-1)
    
    # Parameter t of closest approach to Earth's origin (0,0,0)
    dot_p1_v = np.sum(pos1 * v, axis=-1)
    t = -dot_p1_v / (v_norm_sq + 1e-9)
    
    # Check if closest point lies strictly between pos1 and pos2
    on_segment = (t > 0) & (t < 1)
    
    # Calculate distance to origin at the closest point
    closest_x = pos1[:, 0] + t * v[:, 0]
    closest_y = pos1[:, 1] + t * v[:, 1]
    closest_z = pos1[:, 2] + t * v[:, 2]
    dist2 = closest_x**2 + closest_y**2 + closest_z**2
    
    # Blocked if on segment and closest approach is inside Earth + atmosphere
    min_r = EARTH_RADIUS_KM + ATMOSPHERE_MARGIN_KM
    blocked = on_segment & (dist2 < min_r**2)
    return ~blocked

class TopologySeries:
    def __init__(self, config, constellation):
        self.config = config
        self.constellation = constellation
        self.dt = config['simulation']['timestep_s']
        self.duration = config['simulation']['duration_s']
        
        self.isl_max_range = config['constellation']['isl_max_range_km']
        self.min_elevation = config['constellation']['minimum_elevation_deg']
        
        self.gs_info = config['ground_stations']
        self.gs_ecef = self._compute_gs_ecef()
        
        # Precompute logical ISL edges (+Grid pattern)
        self.logical_isl = self._build_logical_isl()
        
        self._cache = {}
        
    def _compute_gs_ecef(self):
        """Convert Ground Station lat/lon to ECEF coordinates."""
        gs_coords = []
        for gs in self.gs_info:
            lat = np.radians(gs['lat'])
            lon = np.radians(gs['lon'])
            # Assuming ground stations are at altitude 0
            x = EARTH_RADIUS_KM * np.cos(lat) * np.cos(lon)
            y = EARTH_RADIUS_KM * np.cos(lat) * np.sin(lon)
            z = EARTH_RADIUS_KM * np.sin(lat)
            gs_coords.append((x, y, z))
        return np.array(gs_coords)

    def _build_logical_isl(self):
        """
        Determine potential +Grid ISL edges at t=0 to fix logical neighbors.
        Intra-plane: i to i+1
        Inter-plane: closest sat in adjacent plane.
        *Crucial*: No links across the seam (plane 0 to plane P-1).
        """
        P = self.constellation.planes
        S = self.constellation.sats_per_plane
        edges = []
        
        # 1. Intra-plane links
        for p in range(P):
            for s in range(S):
                u = p * S + s
                v = p * S + ((s + 1) % S)
                edges.append((u, v, 'intra'))
                
        # 2. Inter-plane links
        pos0 = self.constellation.get_ecef_positions(0.0)
        # Note: p goes up to P-2. We strictly DO NOT connect P-1 back to 0. (Seam handling)
        for p in range(P - 1): 
            for s in range(S):
                u = p * S + s
                # Find closest satellite in plane p+1 to establish the permanent logical link
                candidates = np.arange((p+1)*S, (p+2)*S)
                dists = np.linalg.norm(pos0[candidates] - pos0[u], axis=1)
                v = candidates[np.argmin(dists)]
                edges.append((u, v, 'inter'))
                
        return edges

    def get_snapshot(self, t):
        """Returns a NetworkX graph representing the topology at time t."""
        if t in self._cache:
            return self._cache[t]
            
        pos = self.constellation.get_ecef_positions(t)
        
        # Compute absolute latitude in degrees
        norms = np.linalg.norm(pos, axis=1)
        abs_lats = np.abs(np.degrees(np.arcsin(np.clip(pos[:, 2] / norms, -1.0, 1.0))))
        
        G = nx.Graph()
        
        # Add Satellite Nodes
        for i in range(self.constellation.n_sats):
            G.add_node(f"sat_{i}", type='satellite', pos=pos[i])
            
        # Add ISL Edges (vectorized check)
        if self.logical_isl:
            u_indices = np.array([e[0] for e in self.logical_isl])
            v_indices = np.array([e[1] for e in self.logical_isl])
            types = np.array([e[2] for e in self.logical_isl])
            
            p1 = pos[u_indices]
            p2 = pos[v_indices]
            
            dists = np.linalg.norm(p2 - p1, axis=1)
            los_ok = has_los(p1, p2)
            valid = (dists <= self.isl_max_range) & los_ok
            
            max_lat = self.config['constellation'].get('isl_inter_plane_max_lat_deg')
            if max_lat is not None:
                lat1 = abs_lats[u_indices]
                lat2 = abs_lats[v_indices]
                lat_ok = (lat1 <= max_lat) & (lat2 <= max_lat)
                is_inter = (types == 'inter')
                lat_ok_final = np.where(is_inter, lat_ok, True)
                valid = valid & lat_ok_final
            
            for i in range(len(self.logical_isl)):
                if valid[i]:
                    u, v, ltype = self.logical_isl[i]
                    d = dists[i]
                    G.add_edge(f"sat_{u}", f"sat_{v}", 
                               distance_km=d, 
                               propagation_delay_ms=(d / SPEED_OF_LIGHT_KM_S) * 1000.0,
                               link_type=ltype,
                               available=True)
                               
        # Add Ground Stations and GSLs
        for idx, gs in enumerate(self.gs_info):
            gs_name = f"gs_{gs['name']}"
            G.add_node(gs_name, type='ground_station', pos=self.gs_ecef[idx])
            
            gs_pos = self.gs_ecef[idx]
            vec = pos - gs_pos
            vec_norm = np.linalg.norm(vec, axis=1)
            gs_norm = np.linalg.norm(gs_pos)
            
            # Elevation angle calculation
            dot = np.sum(vec * (gs_pos / gs_norm), axis=1)
            sin_elev = np.clip(dot / vec_norm, -1.0, 1.0)
            elev = np.degrees(np.arcsin(sin_elev))
            
            # Connect if above minimum elevation
            valid_sats = np.where(elev >= self.min_elevation)[0]
            for s in valid_sats:
                d = vec_norm[s]
                G.add_edge(gs_name, f"sat_{s}",
                           distance_km=d,
                           propagation_delay_ms=(d / SPEED_OF_LIGHT_KM_S) * 1000.0,
                           link_type='gsl',
                           available=True)
                           
        self._cache[t] = G
        return G
        
    def link_remaining_lifetime(self, u, v, current_t):
        """
        Returns how many seconds from current_t the link (u,v) will remain continuously available.
        Uses cached snapshots or generates them as it looks ahead.
        """
        t = current_t
        if not self.get_snapshot(t).has_edge(u, v):
            return 0.0
            
        lifetime = 0.0
        t += self.dt
        while t <= self.duration:
            G = self.get_snapshot(t)
            if not G.has_edge(u, v):
                break
            lifetime += self.dt
            t += self.dt
            
        return lifetime
