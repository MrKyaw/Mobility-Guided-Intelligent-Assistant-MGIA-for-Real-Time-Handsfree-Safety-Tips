import carla
import threading
import queue
import cv2
from ultralytics import YOLO
import numpy as np
import os
# 初始化 YOLOv8 模型
model = YOLO("yolov8n.pt")

# 共享帧队列（设置最大容量防止内存溢出）
frame_queue = queue.Queue(maxsize=10)

def carla_camera_thread():
    # 连接 CARLA 服务器
    client = carla.Client(os.environ.get('CARLA_HOST', "localhost"), 2000)
    world = client.get_world()

    # 生成车辆和摄像头
    vehicle_bp = world.get_blueprint_library().filter('vehicle.*.*')[0]
    vehicle = world.spawn_actor(vehicle_bp, world.get_map().get_spawn_points()[0])
    
    # 配置摄像头
    camera_bp = world.get_blueprint_library().find('sensor.camera.rgb')
    camera = world.spawn_actor(camera_bp, carla.Transform(carla.Location(x=1.5, z=2.4)), attach_to=vehicle)

    # 摄像头回调函数：将帧放入队列
    def camera_callback(image):
        try:
            frame = np.frombuffer(image.raw_data, dtype=np.uint8)
            frame = frame.reshape((image.height, image.width, 4))[:, :, :3]  # 转为RGB
            frame_queue.put(frame, block=False)  # 非阻塞写入
        except queue.Full:
            pass  # 丢弃过时的帧

    camera.listen(camera_callback)
    
    # 保持主线程运行
    while True:
        world.wait_for_tick()

def yolo_inference_thread():
    while True:
        if not frame_queue.empty():
            frame = frame_queue.get()
            results = model(frame)
            annotated_frame = results[0].plot()
            cv2.imshow("YOLOv8 Detection", annotated_frame)
            cv2.waitKey(1)

# 启动线程
carla_thread = threading.Thread(target=carla_camera_thread, daemon=True)
yolo_thread = threading.Thread(target=yolo_inference_thread, daemon=True)
carla_thread.start()
yolo_thread.start()

# 主线程等待退出
try:
    while True:
        pass
except KeyboardInterrupt:
    cv2.destroyAllWindows()