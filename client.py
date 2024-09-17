import grpc
import my_uber_pb2
import my_uber_pb2_grpc
import time

def run_client():
    channel = grpc.insecure_channel('localhost:50051')
    stub = my_uber_pb2_grpc.TaxiServiceStub(channel)

    while True:
        response = stub.AssignService(my_uber_pb2.Empty())
        if response.assigned:
            print("Servicio asignado correctamente")
        else:
            print("No habia taxis disponibles para el servicio")
        time.sleep(60)  # Solicita servicio cada 60 segundos

if __name__ == '__main__':
    run_client()