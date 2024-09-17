import grpc
import my_uber_pb2
import my_uber_pb2_grpc
import time
import random
import sys

class Taxi:
    def __init__(self, taxi_id, grid_n, grid_m, initial_x, initial_y, speed):
        self.id = taxi_id
        self.grid_n = grid_n
        self.grid_m = grid_m
        self.x = initial_x
        self.y = initial_y
        self.speed = speed
        self.channel = grpc.insecure_channel('localhost:50051')
        self.stub = my_uber_pb2_grpc.TaxiServiceStub(self.channel)

    def register(self):
        request = my_uber_pb2.TaxiInfo(
            id=self.id, grid_n=self.grid_n, grid_m=self.grid_m,
            initial_x=self.x, initial_y=self.y, speed=self.speed
        )
        response = self.stub.RegisterTaxi(request)
        print(f"Taxi {self.id} registrado: {response.success}")

    def update_position(self):
        if self.speed > 0:
            direction = random.choice(['N', 'S', 'E', 'W'])
            if direction == 'N' and self.y < self.grid_m:
                self.y += 1
            elif direction == 'S' and self.y > 0:
                self.y -= 1
            elif direction == 'E' and self.x < self.grid_n:
                self.x += 1
            elif direction == 'W' and self.x > 0:
                self.x -= 1

        request = my_uber_pb2.Position(taxi_id=self.id, x=self.x, y=self.y)
        response = self.stub.UpdatePosition(request)
        print(f"Taxi {self.id} posicion actualizada a ({self.x}, {self.y}): {response.received}")

    def run(self):
        self.register()
        while True:
            self.update_position()
            time.sleep(30)  # Actualiza la posicion cada 30 segundos

if __name__ == '__main__':
    if len(sys.argv) != 7:
        print("Usage: python taxi.py <taxi_id> <grid_n> <grid_m> <initial_x> <initial_y> <speed>")
        sys.exit(1)

    taxi_id, grid_n, grid_m, initial_x, initial_y, speed = map(int, sys.argv[1:])
    taxi = Taxi(taxi_id, grid_n, grid_m, initial_x, initial_y, speed)
    taxi.run()