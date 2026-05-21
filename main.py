# pyright: reportAttributeAccessIssue=false
# pyright: reportOptionalMemberAccess=false

from direct.showbase.ShowBase import ShowBase
from panda3d.core import AmbientLight, DirectionalLight, Vec4
from panda3d.core import NodePath, LineSegs, Material, TextureStage, TextNode
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
        segs.setColor(0.75, 0.85, 1.0, 1.0)
        segs.setThickness(1.5)

        if points:
            segs.moveTo(*points[0])
            for p in points[1:]:
                segs.drawTo(*p)

        orbit = NodePath(segs.create())
        orbit.reparentTo(self.render)
        self.orbit = orbit

    def set_selected(self, selected: bool) -> None:
        if self.model is not None and not self.model.isEmpty():
            if selected:
                self.model.setScale(0.085)
                self.model.setColor(1.0, 0.95, 0.2, 1.0)
            else:
                self.model.setScale(0.06)
                self.model.setColor(*self.color)

        if self.orbit is not None and not self.orbit.isEmpty():
            if selected:
                self.orbit.setColorScale(1.2, 1.2, 0.6, 1.0)
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
        self.selected_idx = len(self.satellites) - 1
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

        self.setBackgroundColor(0.02, 0.02, 0.04, 1)
        self.disableMouse()

        self.setup_lighting()
        self.setup_origin_marker()
        self.setup_earth()

        self.sim_time = datetime.now(timezone.utc)
        self.time_scale = 1.0

        self.sat_manager = SatelliteManager(self.render, self.loader, max_satellites=10)
        self.sat_manager.add_next_satellite(self.sim_time)

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

        self.money_ui = OnscreenText(
            text="",
            pos=(1.28, 0.95),
            scale=0.06,
            fg=(0.2, 1.0, 0.2, 1.0),
            align=TextNode.ARight,
        )

        self.pending_purchases: list[dict] = []

        self.purchase_altitude_km = 200000
        self.purchase_inclination_deg = 45.0
        self.purchase_size = 1.0

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

        self.accept("n", self.add_next_satellite)
        self.accept("m", self.remove_selected_satellite)
        self.accept("tab", self.cycle_selected_satellite)
        self.accept("c", self.clear_satellites)

        self.accept("e", self.add_next_satellite)
        self.accept("q", self.cycle_selected_back)

        self.taskMgr.add(self.drag_task, "DragTask")
        self.taskMgr.add(self.camera_smooth_task, "CameraSmoothTask")
        self.taskMgr.add(self.zoom_task, "ZoomTask")
        self.taskMgr.add(self.update_simulation_task, "UpdateSimulation")
        self.taskMgr.add(self.update_hud_task, "UpdateHud")

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
        dt = globalClock.getDt()
        self.sim_time += timedelta(seconds=dt * self.time_scale)

        self.earth_root.setH(-gmst_degrees(self.sim_time))
        self.sat_manager.update_simulation(self.sim_time)

        return Task.cont

    def update_hud_task(self, __task__):
        selected = self.sat_manager.get_selected_record()
        selected_id = selected["OBJECT_ID"] if selected else "None"

        self.hud.setText(
            f"Sim time: {self.sim_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Time scale: {self.time_scale:.2f}x\n"
            f"Satellites: {len(self.sat_manager.satellites)}/{self.sat_manager.max_satellites}\n"
            f"Selected: {selected_id}\n"
            f"[n] add  [m] remove  [tab] cycle  [c] clear"
        )

        self.money_ui.setText(f"${self.money}")
        self._refresh_purchase_ui()

        return Task.cont

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
        self.money += int(amount)

    def spend_money(self, amount: int) -> bool:
        amount = int(amount)
        if amount <= 0:
            return True
        if self.money < amount:
            return False
        self.money -= amount
        return True

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
            range=(200.0, 200000.0),
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
            range=(0.0, 180.0),  # degrees
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

        return {
            "kind": "custom",
            "OBJECT_ID": object_id,
            "altitude_km": float(altitude_km),
            "inclination_deg": float(inclination_deg),
            "raan_deg": 0.0,
            "phase_deg": 0.0,
            "epoch_utc": datetime.now(timezone.utc).isoformat(),
            "size": float(size),
            "power": float(size),
        }


if __name__ == "__main__":
    app = EarthViewer()
    app.run()
