import grpc
from concurrent import futures
import my_uber_pb2
import my_uber_pb2_grpc
import random
import time

class TaxiServiceServicer(my_uber_pb2_grpc.TaxiServiceServicer):
    def __init__(self):
        self.taxis = {}

    def RegisterTaxi(self, request, context):
        taxi_id = request.id
        self.taxis[taxi_id] = {
            'position': (request.initial_x, request.initial_y),
            'speed': request.speed
        }
        print(f"Taxi {taxi_id} registrado en la posicion {self.taxis[taxi_id]['position']}")
        return my_uber_pb2.RegistrationResponse(success=True)

    def UpdatePosition(self, request, context):
        taxi_id = request.taxi_id
        new_position = (request.x, request.y)
        if taxi_id in self.taxis:
            self.taxis[taxi_id]['position'] = new_position
            print(f"Taxi {taxi_id} posicion actualizada a {new_position}")
        return my_uber_pb2.UpdateResponse(received=True)

    def AssignService(self, request, context):
        if self.taxis:
            chosen_taxi = random.choice(list(self.taxis.keys()))
            print(f"Asignando servicio al Taxi {chosen_taxi}")
            return my_uber_pb2.ServiceAssignment(assigned=True)
        return my_uber_pb2.ServiceAssignment(assigned=False)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    my_uber_pb2_grpc.add_TaxiServiceServicer_to_server(TaxiServiceServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    print("Servidor iniciado en puerto 50051")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()