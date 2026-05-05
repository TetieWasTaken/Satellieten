# pyright: reportAttributeAccessIssue=false
# pyright: reportOptionalMemberAccess=false

from direct.showbase.ShowBase import ShowBase
from panda3d.core import AmbientLight, DirectionalLight, Vec4
from panda3d.core import NodePath, LineSegs, Material, TextureStage, TextNode
from direct.task import Task
from direct.gui.OnscreenText import OnscreenText
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

        self.money: int = 0

        self.money_ui = OnscreenText(
            text="",
            pos=(1.28, 0.95),
            scale=0.06,
            fg=(0.2, 1.0, 0.2, 1.0),
            align=TextNode.ARight,
        )

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


if __name__ == "__main__":
    app = EarthViewer()
    app.run()
