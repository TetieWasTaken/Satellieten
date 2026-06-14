# pyright: reportAttributeAccessIssue=false
# pyright: reportOptionalMemberAccess=false

from direct.showbase.ShowBase import ShowBase
from panda3d.core import AmbientLight, DirectionalLight, Vec4
from panda3d.core import NodePath, LineSegs, Material, TextureStage, TextNode
from panda3d.core import PNMImage, Texture
from direct.task import Task
from direct.gui.OnscreenText import OnscreenText
from direct.gui.DirectGui import (
    DirectFrame,
    DirectLabel,
    DirectButton,
    DirectSlider,
)
from direct.showbase.ShowBaseGlobal import globalClock

import math
import random
from datetime import datetime, timezone, timedelta

import server
from sphere import make_uv_sphere


def gmst_degrees(dt_utc: datetime) -> float:
    jd = (
        dt_utc - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
    ).total_seconds() / 86400.0 + 2451545.0
    t = (jd - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t**2
        - (t**3 / 38710000.0)
    )
    return gmst % 360.0


class CoverageOverlay:
    def __init__(
        self,
        earth_np: NodePath,
        size: int = 256,
        update_every_n_frames: int = 3,
    ) -> None:
        self.earth_np = earth_np
        self.size = size
        self.update_every_n_frames = update_every_n_frames

        self.frame_counter = 0

        self.img = PNMImage(self.size, self.size)
        self.img.fill(0.0, 0.0, 0.0)
        self.img.alphaFill(0.0)

        self.tex = Texture("coverage_overlay")
        self.tex.load(self.img)
        self.tex.setMinfilter(Texture.FTLinear)
        self.tex.setMagfilter(Texture.FTLinear)

        self.stage = TextureStage("coverage_stage")
        self.stage.setSort(10)

        self.earth_np.setTexture(self.stage, self.tex, 1)
        self.stage.setColor((1, 1, 1, 1))
        self.stage.setMode(TextureStage.MAdd)

        self.earth_np.setTexOffset(self.stage, 0.5, 0.0)

    def _set_px(self, x: int, y: int, r: float, g: float, b: float, a: float) -> None:
        self.img.setXelA(x, y, r, g, b, a)

    def update(self, satellites: list["SatelliteEntity"], viewer) -> dict | None:
        self.frame_counter += 1
        if self.frame_counter % self.update_every_n_frames != 0:
            return

        self.img.fill(0.0, 0.0, 0.0)
        self.img.alphaFill(0.0)

        player_grid = [[0.0] * self.size for _ in range(self.size)]
        enemy_grid = [[0.0] * self.size for _ in range(self.size)]

        for sat in satellites:
            rec = sat.sat_record
            team = rec.get("team", "player")

            m = viewer.satellite_coverage_metrics(rec)
            alt = m["altitude_km"]
            if alt is None:
                continue

            strength = float(m["power_percent"])
            if strength <= 1e-4:
                continue

            sub = viewer.subsatellite_latlon(rec)
            if sub is None:
                continue
            sub_lat, sub_lon = sub

            R = 6371.0
            h = max(0.0, float(alt))
            alpha = math.acos(max(-1.0, min(1.0, R / (R + h))))
            radius_deg = math.degrees(alpha)

            cx = int(((sub_lon + 180.0) / 360.0) * (self.size - 1))
            cy = int(((sub_lat + 90.0) / 180.0) * (self.size - 1))

            rp = int((radius_deg / 180.0) * (self.size - 1))
            rp = max(1, min(self.size // 2, rp))

            if team == "enemy":
                r0, g0, b0 = (1.0, 0.15, 0.15)
            else:
                r0, g0, b0 = (0.15, 0.45, 1.0)

            for dy in range(-rp, rp + 1):
                y = cy + dy
                if y < 0 or y >= self.size:
                    continue

                for dx in range(-rp, rp + 1):
                    if dx * dx + dy * dy > rp * rp:
                        continue

                    x = (cx + dx) % self.size

                    d = math.sqrt(dx * dx + dy * dy) / max(1.0, rp)

                    edge = max(0.0, 1.0 - d)

                    brightness = (0.35 + 0.65 * edge) * (0.35 + 1.35 * strength)
                    brightness = max(0.0, min(1.0, brightness))

                    r = r0 * brightness
                    g = g0 * brightness
                    b = b0 * brightness

                    a = 0.85 * brightness

                    pr, pg, pb, pa = self.img.getXelA(x, y)
                    nr = min(1.0, pr + r)
                    ng = min(1.0, pg + g)
                    nb = min(1.0, pb + b)
                    na = min(1.0, pa + a)

                    self.img.setXelA(x, y, nr, ng, nb, na)

                    contrib = brightness
                    if team == "enemy":
                        enemy_grid[y][x] = min(1.0, enemy_grid[y][x] + contrib)
                    else:
                        player_grid[y][x] = min(1.0, player_grid[y][x] + contrib)

        share_sum = 0.0
        contested = 0

        for y in range(self.size):
            row_p = player_grid[y]
            row_e = enemy_grid[y]
            for x in range(self.size):
                p = row_p[x]
                e = row_e[x]
                tot = p + e
                if tot <= 1e-6:
                    continue
                contested += 1
                share_sum += p / tot

        avg_player_share = (share_sum / contested) if contested > 0 else 0.0
        avg_enemy_share = 1.0 - avg_player_share if contested > 0 else 0.0

        self.tex.load(self.img)

        return {
            "avg_player_share": avg_player_share,
            "avg_enemy_share": avg_enemy_share,
            "contested_tiles": contested,
        }


class SatelliteEntity:
    def __init__(
        self,
        render: NodePath,
        loader,
        sat_record: dict,
        sim_time: datetime,
        color: tuple[float, float, float, float] = (1.0, 0.2, 0.2, 1.0),
    ) -> None:
        self.render = render
        self.loader = loader
        self.sat_record = sat_record
        self.color = color

        self.model: NodePath | None = None
        self.orbit: NodePath | None = None

        self._build_model(sim_time)
        self._build_orbit_line()

    def _build_model(self, sim_time: datetime) -> None:
        model = self.loader.loadModel("models/misc/sphere")
        if model is None or model.isEmpty():
            print("WARNING: Failed to load satellite model.")
            return

        model.reparentTo(self.render)
        model.setScale(0.06)
        model.setColor(*self.color)
        model.setPos(server.sat_record_to_pos(self.sat_record, sim_time))
        self.model = model

        print("Spawned sat at", self.model.getPos())

    def _build_orbit_line(self) -> None:
        points = server.sample_orbit(self.sat_record, samples=240)
        segs = LineSegs()
        team = self.sat_record.get("team", "player")

        if team == "enemy":
            segs.setColor(1.0, 0.25, 0.25, 1.0)
        else:
            segs.setColor(0.25, 0.55, 1.0, 1.0)

        segs.setThickness(1.5)

        if points:
            segs.moveTo(*points[0])
            for p in points[1:]:
                segs.drawTo(*p)

        orbit = NodePath(segs.create())
        orbit.reparentTo(self.render)
        self.orbit = orbit

    def set_selected(self, selected: bool) -> None:
        team = self.sat_record.get("team", "player")

        if self.model is not None and not self.model.isEmpty():
            if selected:
                self.model.setScale(0.11)
                self.model.setColor(1.0, 1.0, 1.0, 1.0)
            else:
                if team == "enemy":
                    self.model.setScale(0.06)
                    self.model.setColor(1.0, 0.25, 0.25, 1.0)
                else:
                    self.model.setScale(0.06)
                    self.model.setColor(0.25, 0.55, 1.0, 1.0)

        if self.orbit is not None and not self.orbit.isEmpty():
            if selected:
                self.orbit.setColorScale(1.6, 1.6, 1.6, 1.0)
            else:
                self.orbit.setColorScale(1.0, 1.0, 1.0, 1.0)

    def update_simulation(self, sim_time: datetime) -> None:
        if self.model is not None and not self.model.isEmpty():
            self.model.setPos(server.sat_record_to_pos(self.sat_record, sim_time))

    def destroy(self) -> None:
        if self.model is not None and not self.model.isEmpty():
            self.model.removeNode()
        self.model = None

        if self.orbit is not None and not self.orbit.isEmpty():
            self.orbit.removeNode()
        self.orbit = None


class SatelliteManager:
    def __init__(self, render: NodePath, loader, max_satellites: int = 10) -> None:
        self.render = render
        self.loader = loader
        self.max_satellites = max_satellites

        self.satellites: list[SatelliteEntity] = []
        self.selected_idx: int = -1
        self.next_spawn_index: int = 0

        self.palette: list[tuple[float, float, float, float]] = [
            (1.0, 0.2, 0.2, 1.0),
            (0.2, 0.8, 1.0, 1.0),
            (0.4, 1.0, 0.4, 1.0),
            (1.0, 0.6, 0.2, 1.0),
            (0.9, 0.4, 1.0, 1.0),
            (1.0, 0.9, 0.3, 1.0),
        ]

    def add_satellite_from_record(self, sat_record: dict, sim_time: datetime) -> bool:
        if len(self.satellites) >= self.max_satellites:
            return False

        color = self.palette[len(self.satellites) % len(self.palette)]
        sat = SatelliteEntity(
            self.render, self.loader, sat_record, sim_time, color=color
        )

        self.satellites.append(sat)
        # self.selected_idx = len(self.satellites) - 1
        self._refresh_selection()

        return True

    def add_satellite_by_index(self, sat_index: int, sim_time: datetime) -> bool:
        if len(self.satellites) >= self.max_satellites:
            return False

        sat_record = server.get_sat_record(sat_index)

        color = self.palette[len(self.satellites) % len(self.palette)]
        sat = SatelliteEntity(
            self.render, self.loader, sat_record, sim_time, color=color
        )

        self.satellites.append(sat)
        self.selected_idx = len(self.satellites) - 1
        self.next_spawn_index = sat_index + 1
        self._refresh_selection()

        return True

    def add_next_satellite(self, sim_time: datetime) -> bool:
        return self.add_satellite_by_index(self.next_spawn_index, sim_time)

    def remove_selected(self) -> None:
        if not self.satellites or self.selected_idx < 0:
            return

        sat = self.satellites.pop(self.selected_idx)
        sat.destroy()

        if not self.satellites:
            self.selected_idx = -1
        else:
            self.selected_idx = min(self.selected_idx, len(self.satellites) - 1)

        self._refresh_selection()

    def clear_all(self) -> None:
        for sat in self.satellites:
            sat.destroy()
        self.satellites.clear()
        self.selected_idx = -1

    def cycle_selected(self, step: int = 1) -> None:
        if not self.satellites:
            return
        self.selected_idx = (self.selected_idx + step) % len(self.satellites)
        self._refresh_selection()

    def set_selected_to_latest(self) -> None:
        if self.satellites:
            self.selected_idx = len(self.satellites) - 1
            self._refresh_selection()

    def update_simulation(self, sim_time: datetime) -> None:
        for sat in self.satellites:
            sat.update_simulation(sim_time)

    def get_selected_record(self) -> dict | None:
        if not self.satellites or self.selected_idx < 0:
            return None
        return self.satellites[self.selected_idx].sat_record

    def _refresh_selection(self) -> None:
        for i, sat in enumerate(self.satellites):
            sat.set_selected(i == self.selected_idx)


class EarthViewer(ShowBase):
    def __init__(self) -> None:
        super().__init__()

        self.game_started = False
        self.game_over = False
        self.game_result = ""

        self.grace_period_s = 180.0
        self.game_started_at: datetime | None = None

        self.setBackgroundColor(0.02, 0.02, 0.04, 1)
        self.disableMouse()

        self.setup_lighting()
        self.setup_origin_marker()
        self.setup_earth()

        self.sim_time = datetime.now(timezone.utc)
        self.time_scale = 64.0

        self.sat_manager = SatelliteManager(
            self.render, self.loader, max_satellites=100
        )

        self.enemy_spawn_interval_s = 3840.0
        self.enemy_next_spawn_time = self.sim_time + timedelta(
            seconds=self.enemy_spawn_interval_s
        )

        self.enemy_galileo_indices: list[int] = list(range(0, 32))
        random.shuffle(self.enemy_galileo_indices)
        self.enemy_galileo_cursor = 0

        self.camera_distance = 12.0
        self.camera_h = 45.0
        self.camera_p = -20.0

        self.target_distance = self.camera_distance
        self.target_h = self.camera_h
        self.target_p = self.camera_p

        self.mouse_sensitivity = 150.0
        self.zoom_speed = 8.0
        self.smooth_factor = 0.15

        self.dragging = False
        self.last_mouse = None
        self.zoom_dir = 0

        self.update_camera()

        self.hud = OnscreenText(
            text="",
            pos=(-1.3, 0.95),
            scale=0.045,
            fg=(0.9, 0.9, 0.9, 1),
            align=TextNode.ALeft,
        )

        self.money: int = 10000
        self.money_float = float(self.money)

        self.money_ui = OnscreenText(
            text="",
            pos=(1.28, 0.95),
            scale=0.06,
            fg=(0.2, 1.0, 0.2, 1.0),
            align=TextNode.ARight,
        )

        self.start_button = DirectButton(
            text="START",
            scale=0.08,
            pos=(0, 0, 0),
            command=self.start_game,
        )

        self.pending_purchases: list[dict] = []

        self.purchase_altitude_km = 550.0
        self.purchase_inclination_deg = 45.0
        self.purchase_size = 1.0

        self.income_per_sec: float = 0.0
        self.base_income_per_sec: float = 250.0

        self.enemy_money: float = 3000.0
        self.enemy_income_per_sec: float = 100.0
        self.enemy_kill_check_interval_s = 30.0
        self.enemy_next_kill_check = datetime.now(timezone.utc) + timedelta(
            seconds=self.enemy_kill_check_interval_s
        )

        self.taskMgr.add(self.enemy_economy_task, "EnemyEconomy")
        self.taskMgr.add(self.enemy_kill_task, "EnemyKillTask")

        self._build_purchase_ui()
        self._refresh_purchase_ui()

        self.accept("mouse1", self.start_drag)
        self.accept("mouse1-up", self.stop_drag)

        self.accept("arrow_up", self.set_zoom_in)
        self.accept("arrow_up-up", self.stop_zoom)
        self.accept("arrow_down", self.set_zoom_out)
        self.accept("arrow_down-up", self.stop_zoom)

        self.accept("]", self.speed_up)
        self.accept("[", self.slow_down)
        self.accept("\\", self.reset_speed)

        self.accept("tab", self.select_next_enemy_satellite)
        self.accept("shift-tab", self.select_prev_enemy_satellite)
        self.accept("backspace", self.select_prev_enemy_satellite)

        self.accept("x", self.player_eliminate_selected_enemy)

        self.taskMgr.add(self.drag_task, "DragTask")
        self.taskMgr.add(self.camera_smooth_task, "CameraSmoothTask")
        self.taskMgr.add(self.zoom_task, "ZoomTask")
        self.taskMgr.add(self.update_simulation_task, "UpdateSimulation")
        self.taskMgr.add(self.update_hud_task, "UpdateHud")

        self.taskMgr.add(self.update_coverage_overlay_task, "UpdateCoverageOverlay")

        self.taskMgr.add(self.earn_money_task, "EarnMoney")

        self.taskMgr.add(self.enemy_spawn_task, "EnemySpawnTask")

        self.event_msg = ""
        self.event_msg_until = datetime.now(timezone.utc)

    def start_game(self) -> None:
        self.game_started = True
        self.game_over = False
        self.game_result = ""

        self.sat_manager.clear_all()
        self.money = 5000.0
        self.money_float = float(self.money)
        self.enemy_money = 3000.0

        self.sim_time = datetime.now(timezone.utc)
        self.enemy_next_spawn_time = self.sim_time + timedelta(
            seconds=self.enemy_spawn_interval_s
        )
        self.enemy_galileo_cursor = 0
        random.shuffle(self.enemy_galileo_indices)

        if hasattr(self, "start_button") and self.start_button:
            self.start_button.hide()

        self.spawn_initial_enemy_galileo()

        self.game_started_at = datetime.now(timezone.utc)

        self.push_event("Game started!")

    def check_end_condition(self) -> None:
        if self.game_over or not self.game_started:
            return

        enemy_left = any(
            s.sat_record.get("team", "player") == "enemy"
            for s in self.sat_manager.satellites
        )

        player_left = any(
            s.sat_record.get("team", "player") == "player"
            for s in self.sat_manager.satellites
        )

        if not enemy_left:
            self.game_over = True
            self.game_result = "You win!"
            self.push_event("All enemy satellites eliminated. You win!", seconds=9999)
            if hasattr(self, "start_button") and self.start_button:
                self.start_button.show()
                self.start_button["text"] = "RESTART"

        if not player_left:
            if self.game_started_at is not None:
                elapsed = (
                    datetime.now(timezone.utc) - self.game_started_at
                ).total_seconds()
                if elapsed < self.grace_period_s:
                    return

            self.game_over = True
            self.game_result = "You lose!"
            self.push_event("Your satellites were eliminated. You lose!", seconds=9999)
            if hasattr(self, "start_button") and self.start_button:
                self.start_button.show()
                self.start_button["text"] = "RESTART"

    def spawn_initial_enemy_galileo(self) -> None:
        if not self.enemy_galileo_indices:
            return

        idx = self.enemy_galileo_indices[self.enemy_galileo_cursor]
        self.enemy_galileo_cursor += 1

        try:
            rec = server.get_sat_record(idx)
        except Exception as e:
            print("[enemy] Failed to spawn initial Galileo:", e)
            return

        rec["team"] = "enemy"

        ok = self.sat_manager.add_satellite_from_record(rec, self.sim_time)
        if ok:
            print(f"[enemy] Spawned initial Galileo index {idx}")

    def spawn_test_enemy_satellite(self) -> None:
        rec = {
            "kind": "custom",
            "team": "enemy",
            "OBJECT_ID": f"ENEMY-{datetime.now(timezone.utc).strftime('%H%M%S')}",
            "altitude_km": 1200.0,
            "inclination_deg": 98.0,
            "raan_deg": 0.0,
            "phase_deg": 0.0,
            "epoch_utc": datetime.now(timezone.utc).isoformat(),
            "size": 1.2,
            "power": 1.2,
        }
        self.sat_manager.add_satellite_from_record(rec, self.sim_time)

    def setup_lighting(self) -> None:
        ambient = AmbientLight("ambient")
        ambient.setColor(Vec4(0.2, 0.2, 0.25, 1))
        ambient_np = self.render.attachNewNode(ambient)

        sun = DirectionalLight("sun")
        sun.setColor(Vec4(1.0, 0.98, 0.95, 1))
        sun_np = self.render.attachNewNode(sun)
        sun_np.setHpr(60, -25, 0)

        self.render.setLight(ambient_np)
        self.render.setLight(sun_np)

    def setup_origin_marker(self) -> None:
        marker = self.loader.loadModel("models/misc/sphere")
        if marker and not marker.isEmpty():
            marker.reparentTo(self.render)
            marker.setScale(0.1)
            marker.setColor(1, 1, 0, 1)
            marker.setPos(0, 0, 0)

    def setup_earth(self) -> None:
        self.earth_root = self.render.attachNewNode("earth_root")

        self.earth = make_uv_sphere(radius=2.0, rings=64, segments=128)
        self.earth.reparentTo(self.earth_root)

        texture = self.loader.loadTexture("Textures/earth.jpg")
        texture.setMagfilter(texture.FTLinear)
        texture.setMinfilter(texture.FTLinearMipmapLinear)
        self.earth.setTexture(texture, 1)

        self.earth.setTexOffset(TextureStage.getDefault(), 0.5, 0)

        mat = Material()
        mat.setShininess(24.0)
        mat.setSpecular(Vec4(0.6, 0.6, 0.6, 1))
        self.earth.setMaterial(mat, 1)

        self.coverage_overlay = CoverageOverlay(
            earth_np=self.earth,
            size=96,
            update_every_n_frames=60,
        )

    def player_eliminate_selected_enemy(self) -> None:
        selected = self.sat_manager.get_selected_record()
        if not selected:
            return
        if selected.get("team", "player") != "enemy":
            return

        cost = self.eliminate_cost(selected)
        if not self.spend_money(cost):
            print(f"[player] Not enough money to eliminate ({cost}).")
            return

        removed = self.eliminate_satellite(selected)
        if removed:
            print(
                f"[player] Eliminated enemy sat {selected.get('OBJECT_ID')} for ${cost}."
            )
            self.push_event(
                f"You eliminated enemy sat {selected.get('OBJECT_ID')} (-${cost})"
            )
        else:
            self.add_money(cost)

    def update_camera(self) -> None:
        h_rad = math.radians(self.camera_h)
        p_rad = math.radians(self.camera_p)

        x = self.camera_distance * math.sin(h_rad) * math.cos(p_rad)
        y = -self.camera_distance * math.cos(h_rad) * math.cos(p_rad)
        z = -self.camera_distance * math.sin(p_rad)

        self.camera.setPos(x, y, z)
        self.camera.lookAt(0, 0, 0)

    def start_drag(self) -> None:
        if self.mouseWatcherNode.hasMouse():
            self.dragging = True
            self.last_mouse = self.mouseWatcherNode.getMouse()

    def stop_drag(self) -> None:
        self.dragging = False
        self.last_mouse = None

    def drag_task(self, __task__):
        if self.dragging and self.mouseWatcherNode.hasMouse():
            current_mouse = self.mouseWatcherNode.getMouse()

            if self.last_mouse is not None:
                dx = current_mouse.getX() - self.last_mouse.getX()
                dy = current_mouse.getY() - self.last_mouse.getY()

                self.target_h -= dx * self.mouse_sensitivity
                self.target_p += dy * self.mouse_sensitivity
                self.target_p = max(-80, min(80, self.target_p))

                self.last_mouse = current_mouse.__class__(
                    current_mouse.getX(), current_mouse.getY()
                )

        return Task.cont

    def push_event(self, text: str, seconds: float = 3.0) -> None:
        self.event_msg = text
        self.event_msg_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    def set_zoom_in(self) -> None:
        self.zoom_dir = -1

    def set_zoom_out(self) -> None:
        self.zoom_dir = 1

    def stop_zoom(self) -> None:
        self.zoom_dir = 0

    def zoom_task(self, __task__):
        if self.zoom_dir != 0:
            dt = globalClock.getDt()
            self.target_distance += self.zoom_dir * self.zoom_speed * dt
            self.target_distance = max(4, min(50, self.target_distance))
        return Task.cont

    def camera_smooth_task(self, __task__):
        self.camera_h += (self.target_h - self.camera_h) * self.smooth_factor
        self.camera_p += (self.target_p - self.camera_p) * self.smooth_factor
        self.camera_distance += (
            self.target_distance - self.camera_distance
        ) * self.smooth_factor
        self.update_camera()
        return Task.cont

    def update_simulation_task(self, __task__):
        if not self.game_started or self.game_over:
            return Task.cont

        dt = globalClock.getDt()
        self.sim_time += timedelta(seconds=dt * self.time_scale)

        self.earth_root.setH(-gmst_degrees(self.sim_time))
        self.sat_manager.update_simulation(self.sim_time)

        self.check_end_condition()

        return Task.cont

    def update_hud_task(self, __task__):
        selected = self.sat_manager.get_selected_record()
        selected_id = selected["OBJECT_ID"] if selected else "None"

        cov_line = ""
        if selected:
            m = self.satellite_coverage_metrics(selected)
            if m["altitude_km"] is not None:
                cov_line = (
                    f"\nCoverage: {m['footprint_fraction'] * 100:.1f}%  "
                    f"Power: {m['power_percent'] * 100:.1f}%  "
                    f"Effective: {m['effective_coverage'] * 100:.1f}%"
                )
            else:
                cov_line = f"\nCoverage: unknown"

        if datetime.now(timezone.utc) > self.event_msg_until:
            self.event_msg = ""

        selected = self.sat_manager.get_selected_record()
        if selected and selected.get("team", "player") == "enemy":
            del_cost = self.eliminate_cost(selected)
            can_afford = self.money >= del_cost
            del_line = f"Delete (X): ${del_cost} " + (
                "[OK]" if can_afford else "[too expensive]"
            )
        else:
            del_line = "Delete (X): select an enemy satellite"

        sel_id = selected["OBJECT_ID"] if selected else "None"

        state_line = "RUNNING" if self.game_started and not self.game_over else "PAUSED"
        if self.game_over:
            state_line = self.game_result

        if self.game_started_at is not None and not self.game_over:
            elapsed = (
                datetime.now(timezone.utc) - self.game_started_at
            ).total_seconds()
            if elapsed < self.grace_period_s:
                grace_left = self.grace_period_s - elapsed
                grace_line = f"Grace: {grace_left:.0f}s"
            else:
                grace_line = ""
        else:
            grace_line = ""

        self.hud.setText(
            f"Sim time: {self.sim_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Time scale: {self.time_scale:.2f}x\n"
            f"Satellites: {len(self.sat_manager.satellites)}/{self.sat_manager.max_satellites}\n"
            f"Selected enemy: {sel_id}\n"
            f"{del_line}\n"
            f"Select enemy: [Tab]=next  [Backspace]=prev\n"
            f"Income: ${self.income_per_sec:.0f}/s\n"
            f"Enemy money: ${self.enemy_money:,.0f}\n"
            f"State: {state_line}\n"
            f"{grace_line}\n"
            f"{self.event_msg}"
        )

        self.money_ui.setText(f"${self.money}")
        self._refresh_purchase_ui()

        return Task.cont

    def enemy_spawn_task(self, __task__):
        if (
            not self.game_started
            or self.game_over
            or self.sim_time < self.enemy_next_spawn_time
        ):
            return Task.cont

        while self.sim_time >= self.enemy_next_spawn_time:
            self.enemy_next_spawn_time += timedelta(seconds=self.enemy_spawn_interval_s)

        if self.enemy_galileo_cursor >= len(self.enemy_galileo_indices):
            return Task.cont

        idx = self.enemy_galileo_indices[self.enemy_galileo_cursor]

        try:
            rec = server.get_sat_record(idx)
        except Exception:
            print("[enemy] Failed to fetch Galileo record.")
            return Task.cont

        cost = self.estimate_sat_build_cost(rec)
        print(f"[enemy] spawn cost: {cost}")

        if self.enemy_money < cost:
            print(
                f"[enemy] Can't afford Galileo index {idx}. (cost: {cost} | money: {self.enemy_money})"
            )
            return Task.cont

        rec["team"] = "enemy"
        ok = self.sat_manager.add_satellite_from_record(rec, self.sim_time)
        if ok:
            self.enemy_money -= cost
            self.enemy_galileo_cursor += 1
            print(f"[enemy] Spawned Galileo index {idx}")
        else:
            print("[enemy] Max satellites reached")

        return Task.cont

    def enemy_economy_task(self, __task__):
        dt = globalClock.getDt()
        self.enemy_money += self.enemy_income_per_sec * dt
        return Task.cont

    def enemy_kill_task(self, __task__):
        if not self.game_started or self.game_over:
            return Task.cont

        elapsed = (datetime.now(timezone.utc) - self.game_started_at).total_seconds()
        if elapsed < self.grace_period_s:
            return Task.cont

        now = datetime.now(timezone.utc)
        if now < self.enemy_next_kill_check:
            return Task.cont
        self.enemy_next_kill_check = now + timedelta(
            seconds=self.enemy_kill_check_interval_s
        )

        player_sats = [
            s.sat_record
            for s in self.sat_manager.satellites
            if s.sat_record.get("team", "player") == "player"
        ]
        if not player_sats:
            return Task.cont

        target = random.choice(player_sats)
        cost = self.eliminate_cost(target)

        if self.enemy_money < cost:
            return Task.cont

        self.enemy_money -= cost
        removed = self.eliminate_satellite(target)
        if removed:
            print(
                f"[enemy] Eliminated player sat {target.get('OBJECT_ID')} (cost ${cost})."
            )
            self.push_event(f"Your satellite {target.get('OBJECT_ID')} was eliminated!")

        return Task.cont

    def _enemy_indices(self) -> list[int]:
        return [
            i
            for i, s in enumerate(self.sat_manager.satellites)
            if s.sat_record.get("team", "player") == "enemy"
        ]

    def select_next_enemy_satellite(self) -> None:
        enemies = self._enemy_indices()
        if not enemies:
            self.sat_manager.selected_idx = -1
            self.sat_manager._refresh_selection()
            self.push_event("No enemy satellites to select.")
            return

        if self.sat_manager.selected_idx not in enemies:
            self.sat_manager.selected_idx = enemies[0]
        else:
            k = enemies.index(self.sat_manager.selected_idx)
            self.sat_manager.selected_idx = enemies[(k + 1) % len(enemies)]

        self.sat_manager._refresh_selection()

    def select_prev_enemy_satellite(self) -> None:
        enemies = self._enemy_indices()
        if not enemies:
            self.sat_manager.selected_idx = -1
            self.sat_manager._refresh_selection()
            self.push_event("No enemy satellites to select.")
            return

        if self.sat_manager.selected_idx not in enemies:
            self.sat_manager.selected_idx = enemies[-1]
        else:
            k = enemies.index(self.sat_manager.selected_idx)
            self.sat_manager.selected_idx = enemies[(k - 1) % len(enemies)]

        self.sat_manager._refresh_selection()

    def speed_up(self) -> None:
        self.time_scale *= 2.0

    def slow_down(self) -> None:
        self.time_scale = max(0.25, self.time_scale / 2.0)

    def reset_speed(self) -> None:
        self.time_scale = 1.0

    def add_next_satellite(self) -> None:
        ok = self.sat_manager.add_next_satellite(self.sim_time)
        if not ok:
            print(f"Reached max satellites ({self.sat_manager.max_satellites}).")

    def remove_selected_satellite(self) -> None:
        self.sat_manager.remove_selected()

    def cycle_selected_satellite(self) -> None:
        self.sat_manager.cycle_selected(step=1)

    def cycle_selected_back(self) -> None:
        self.sat_manager.cycle_selected(step=-1)

    def clear_satellites(self) -> None:
        self.sat_manager.clear_all()

    def add_money(self, amount: int) -> None:
        self.money_float += float(amount)
        self.money += int(amount)

    def spend_money(self, amount: int) -> bool:
        amount = int(amount)
        if amount <= 0:
            return True
        if self.money < amount:
            return False

        self.money_float -= float(amount)
        self.money -= amount

        return True

    def _infer_altitude_km_from_pos(self, sat_record: dict) -> float | None:
        try:
            x, y, z = server.sat_record_to_pos(sat_record, self.sim_time)
        except Exception:
            return None

        r_units = math.sqrt(
            float(x) * float(x) + float(y) * float(y) + float(z) * float(z)
        )
        if r_units <= 1e-8:
            return None

        r_km = r_units * (server.EARTH_RADIUS_KM / server.EARTH_RADIUS_UNITS)
        alt_km = r_km - server.EARTH_RADIUS_KM
        return max(0.0, alt_km)

    def _build_purchase_ui(self) -> None:
        self.purchase_frame = DirectFrame(
            frameColor=(0.05, 0.05, 0.08, 0.85),
            frameSize=(-0.62, 0.62, -0.34, 0.34),
            pos=(-0.62, 0, -0.62),
            scale=0.82,
        )

        self.purchase_title = DirectLabel(
            parent=self.purchase_frame,
            text="Purchase Satellite",
            scale=0.06,
            pos=(0, 0, 0.26),
            text_fg=(1, 1, 1, 1),
            frameColor=(0, 0, 0, 0),
        )

        self.alt_label = DirectLabel(
            parent=self.purchase_frame,
            text="Altitude (km):",
            scale=0.045,
            pos=(-0.50, 0, 0.14),
            text_align=TextNode.ALeft,
            text_fg=(0.9, 0.9, 0.9, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.alt_value = DirectLabel(
            parent=self.purchase_frame,
            text="",
            scale=0.045,
            pos=(0.52, 0, 0.14),
            text_align=TextNode.ARight,
            text_fg=(0.9, 0.9, 0.9, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.alt_slider = DirectSlider(
            parent=self.purchase_frame,
            range=(200.0, 40000.0),
            value=self.purchase_altitude_km,
            pageSize=25.0,
            scale=0.5,
            pos=(0.0, 0, 0.08),
            command=self._on_altitude_changed,
        )

        self.inc_label = DirectLabel(
            parent=self.purchase_frame,
            text="Inclination (deg):",
            scale=0.045,
            pos=(-0.50, 0, -0.02),
            text_align=TextNode.ALeft,
            text_fg=(0.9, 0.9, 0.9, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.inc_value = DirectLabel(
            parent=self.purchase_frame,
            text="",
            scale=0.045,
            pos=(0.52, 0, -0.02),
            text_align=TextNode.ARight,
            text_fg=(0.9, 0.9, 0.9, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.inc_slider = DirectSlider(
            parent=self.purchase_frame,
            range=(0.0, 180.0),
            value=self.purchase_inclination_deg,
            pageSize=5.0,
            scale=0.5,
            pos=(0.0, 0, -0.08),
            command=self._on_inclination_changed,
        )

        self.size_label = DirectLabel(
            parent=self.purchase_frame,
            text="Size / Power:",
            scale=0.045,
            pos=(-0.50, 0, -0.18),
            text_align=TextNode.ALeft,
            text_fg=(0.9, 0.9, 0.9, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.size_value = DirectLabel(
            parent=self.purchase_frame,
            text="",
            scale=0.045,
            pos=(0.52, 0, -0.18),
            text_align=TextNode.ARight,
            text_fg=(0.9, 0.9, 0.9, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.size_slider = DirectSlider(
            parent=self.purchase_frame,
            range=(0.5, 3.0),
            value=self.purchase_size,
            pageSize=0.1,
            scale=0.5,
            pos=(0.0, 0, -0.24),
            command=self._on_size_changed,
        )

        self.cost_label = DirectLabel(
            parent=self.purchase_frame,
            text="Cost:",
            scale=0.05,
            pos=(-0.50, 0, -0.30),
            text_align=TextNode.ALeft,
            text_fg=(1.0, 0.9, 0.3, 1),
            frameColor=(0, 0, 0, 0),
        )
        self.cost_value = DirectLabel(
            parent=self.purchase_frame,
            text="",
            scale=0.05,
            pos=(0.52, 0, -0.30),
            text_align=TextNode.ARight,
            text_fg=(1.0, 0.9, 0.3, 1),
            frameColor=(0, 0, 0, 0),
        )

        self.buy_button = DirectButton(
            parent=self.purchase_frame,
            text="BUY",
            scale=0.06,
            pos=(0.30, 0, -0.30),
            command=self._buy_satellite_clicked,
        )
        self.reset_button = DirectButton(
            parent=self.purchase_frame,
            text="Reset",
            scale=0.05,
            pos=(0.02, 0, -0.30),
            command=self._reset_purchase_defaults,
        )

        self.purchase_msg = DirectLabel(
            parent=self.purchase_frame,
            text="",
            scale=0.045,
            pos=(0.0, 0, 0.205),
            text_fg=(1.0, 0.5, 0.5, 1),
            frameColor=(0, 0, 0, 0),
        )

    def _reset_purchase_defaults(self) -> None:
        self.purchase_altitude_km = 550.0
        self.purchase_inclination_deg = 53.0
        self.purchase_size = 1.0
        self.alt_slider["value"] = self.purchase_altitude_km
        self.inc_slider["value"] = self.purchase_inclination_deg
        self.size_slider["value"] = self.purchase_size
        self.purchase_msg["text"] = ""
        self._refresh_purchase_ui()

    def _on_altitude_changed(self, *args) -> None:
        self.purchase_altitude_km = float(self.alt_slider["value"])
        self.purchase_msg["text"] = ""
        self._refresh_purchase_ui()

    def _on_inclination_changed(self, *args) -> None:
        self.purchase_inclination_deg = float(self.inc_slider["value"])
        self.purchase_msg["text"] = ""
        self._refresh_purchase_ui()

    def _on_size_changed(self, *args) -> None:
        self.purchase_size = float(self.size_slider["value"])
        self.purchase_msg["text"] = ""
        self._refresh_purchase_ui()

    def _estimate_satellite_cost(
        self, altitude_km: float, inclination_deg: float, size: float
    ) -> int:
        base = 250
        altitude_cost = int(max(0.0, altitude_km - 200.0) * 0.35)
        incl_cost = int(abs(inclination_deg - 53.0) * 3.0)
        size_cost = int(200 * (size**2))
        return base + altitude_cost + incl_cost + size_cost

    def _refresh_purchase_ui(self) -> None:
        alt = self.purchase_altitude_km
        inc = self.purchase_inclination_deg
        size = self.purchase_size

        self.alt_value["text"] = f"{alt:.0f}"
        self.inc_value["text"] = f"{inc:.0f}"
        self.size_value["text"] = f"{size:.2f}x"

        cost = self._estimate_satellite_cost(alt, inc, size)
        self.cost_value["text"] = f"${cost}"

        if self.money >= cost:
            self.buy_button["state"] = "normal"
            self.cost_value["text_fg"] = (1.0, 0.9, 0.3, 1)
        else:
            self.buy_button["state"] = "disabled"
            self.cost_value["text_fg"] = (1.0, 0.45, 0.45, 1)

    def _buy_satellite_clicked(self, *args) -> None:
        if not self.game_started or self.game_over:
            return

        alt = self.purchase_altitude_km
        inc = self.purchase_inclination_deg
        size = self.purchase_size
        cost = self._estimate_satellite_cost(alt, inc, size)

        if not self.spend_money(cost):
            self.purchase_msg["text"] = "Not enough money!"
            self._refresh_purchase_ui()
            return

        sat_record = self._make_purchased_sat_record(alt, inc, size)

        ok = self.sat_manager.add_satellite_from_record(sat_record, self.sim_time)
        if not ok:
            self.add_money(cost)
            self.purchase_msg["text"] = (
                f"Max satellites reached ({self.sat_manager.max_satellites})."
            )
            self._refresh_purchase_ui()
            return

        order = {
            "object_id": sat_record["OBJECT_ID"],
            "altitude_km": alt,
            "inclination_deg": inc,
            "size": size,
            "cost": cost,
            "purchased_at_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ),
        }
        self.pending_purchases.append(order)

        self.purchase_msg["text"] = (
            f"Purchased + spawned! (total: {len(self.pending_purchases)})"
        )
        self._refresh_purchase_ui()

    def _make_purchased_sat_record(
        self, altitude_km: float, inclination_deg: float, size: float
    ) -> dict:
        object_id = f"BUY-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

        raan_deg = random.uniform(0.0, 360.0)
        phase_deg = random.uniform(0.0, 360.0)

        build_cost = self._estimate_satellite_cost(altitude_km, inclination_deg, size)

        return {
            "kind": "custom",
            "team": "player",
            "OBJECT_ID": object_id,
            "altitude_km": float(altitude_km),
            "inclination_deg": float(inclination_deg),
            "raan_deg": float(raan_deg),
            "phase_deg": float(phase_deg),
            "epoch_utc": datetime.now(timezone.utc).isoformat(),
            "size": float(size),
            "power": float(size),
            "build_cost": int(build_cost),
        }

    def estimate_sat_build_cost(self, record: dict) -> int:
        if "build_cost" in record:
            return int(record["build_cost"])

        alt = self._satellite_altitude_km(record) or 20000.0
        strength = self._satellite_strength(record)

        base = 2000
        altitude_cost = int(max(0.0, alt - 200.0) * 0.08)
        strength_cost = int(2500 * (float(strength) ** 1.4))
        return base + altitude_cost + strength_cost

    def eliminate_cost(self, record: dict) -> int:
        kill_cost_multiplier = 3.0
        return int(kill_cost_multiplier * self.estimate_sat_build_cost(record))

    def eliminate_satellite(self, record: dict) -> bool:
        obj_id = record.get("OBJECT_ID")
        if not obj_id:
            return False

        for i, sat in enumerate(list(self.sat_manager.satellites)):
            if sat.sat_record.get("OBJECT_ID") == obj_id:
                self.sat_manager.selected_idx = i
                self.sat_manager.remove_selected()
                return True

        return False

    def _satellite_altitude_km(self, record: dict) -> float | None:
        if record.get("kind") == "custom":
            return float(record.get("altitude_km", 0.0))

        return self._infer_altitude_km_from_pos(record)

    def _satellite_strength(self, record: dict) -> float:
        if "power" in record:
            return float(record.get("power", 1.0))
        if "size" in record:
            return float(record.get("size", 1.0))

        return 3.0

    def coverage_footprint_fraction(self, altitude_km: float) -> float:
        R = 6371.0
        h = max(0.0, float(altitude_km))
        cos_alpha = R / (R + h) if (R + h) > 0 else 1.0
        cos_alpha = max(-1.0, min(1.0, cos_alpha))
        alpha = math.acos(cos_alpha)
        return (1.0 - math.cos(alpha)) / 2.0

    def coverage_power_percent(self, altitude_km: float, strength: float) -> float:
        h = max(0.0, float(altitude_km))
        s = max(0.0, float(strength))

        h0 = 800.0
        gain = 3.5

        raw = gain * s / ((1.0 + (h / h0)) ** 2)

        p = 1.0 - math.exp(-raw)
        return max(0.0, min(1.0, p))

    def satellite_coverage_metrics(self, record: dict) -> dict:
        alt = self._satellite_altitude_km(record)
        strength = self._satellite_strength(record)

        if alt is None:
            return {
                "altitude_km": None,
                "strength": strength,
                "footprint_fraction": 0.0,
                "power_percent": 0.0,
                "effective_coverage": 0.0,
            }

        footprint = self.coverage_footprint_fraction(alt)
        power = self.coverage_power_percent(alt, strength)
        effective = footprint * power

        return {
            "altitude_km": alt,
            "strength": strength,
            "footprint_fraction": footprint,
            "power_percent": power,
            "effective_coverage": effective,
            "note": "",
        }

    def subsatellite_latlon(self, sat_record: dict) -> tuple[float, float] | None:
        try:
            x, y, z = server.sat_record_to_pos(sat_record, self.sim_time)
        except Exception:
            return None

        p_render = self.render.getRelativePoint(self.render, (x, y, z))
        p_earth = self.earth_root.getRelativePoint(self.render, p_render)

        vx, vy, vz = float(p_earth.x), float(p_earth.y), float(p_earth.z)
        r = math.sqrt(vx * vx + vy * vy + vz * vz)
        if r <= 1e-8:
            return None

        lat = -math.degrees(math.asin(vz / r))
        lon = math.degrees(math.atan2(vy, vx))
        return (lat, lon)

    def _tile_in_footprint(
        self,
        altitude_km: float,
        tile_lat: float,
        tile_lon: float,
        sub_lat: float,
        sub_lon: float,
    ) -> bool:
        R = 6371.0
        h = max(0.0, float(altitude_km))

        cos_alpha = R / (R + h)
        cos_alpha = max(-1.0, min(1.0, cos_alpha))
        alpha = math.acos(cos_alpha)

        lat1 = math.radians(tile_lat)
        lon1 = math.radians(tile_lon)
        lat2 = math.radians(sub_lat)
        lon2 = math.radians(sub_lon)

        cos_d = math.sin(lat1) * math.sin(lat2) + math.cos(lat1) * math.cos(
            lat2
        ) * math.cos(lon1 - lon2)
        cos_d = max(-1.0, min(1.0, cos_d))
        d = math.acos(cos_d)

        return d <= alpha

    def update_coverage_overlay_task(self, __task__):
        stats = self.coverage_overlay.update(self.sat_manager.satellites, self)
        if stats is not None:
            p_share = float(stats["avg_player_share"])
            e_share = float(stats["avg_enemy_share"])

            self.income_per_sec = self.base_income_per_sec * p_share
            self.enemy_income_per_sec = self.base_income_per_sec * e_share
        return Task.cont

    def earn_money_task(self, __task__):
        dt = globalClock.getDt()
        self.money_float += self.income_per_sec * dt
        self.money = int(self.money_float)
        return Task.cont


if __name__ == "__main__":
    app = EarthViewer()
    app.run()
