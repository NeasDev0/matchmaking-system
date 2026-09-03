import docker
import socket
import time
import os
import redis

DOCKER_IMAGE = "phantom_server:latest"
CONTAINER_PREFIX = "game_server_"
MIN_PORT = 7000
MAX_PORT = 7100
WARM_POOL_SIZE = 3      # сколько СВОБОДНЫХ (waiting) серверов держать готовыми
PUBLIC_IP = "127.0.0.1"

SERVERS_AVAILABLE_KEY = "servers:available"

client = docker.from_env()
r = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)


class PortManager:
    def __init__(self, min_port: int, max_port: int):
        self.min_port = min_port
        self.max_port = max_port
        # Сет портов, которые заняты прямо сейчас (в памяти)
        self.allocated_ports: set[int] = set()

    def sync_with_docker(self, docker_client: docker.DockerClient, container_prefix: str):
        """
        Синхронизирует память с реально работающими контейнерами.
        Освобождает порты серверов, которые завершили работу.
        """
        docker_used_ports = set()
        try:
            containers = docker_client.containers.list(filters={"name": container_prefix})
            for c in containers:
                for inner_port, host_bindings in (c.ports or {}).items():
                    if host_bindings:
                        for binding in host_bindings:
                            docker_used_ports.add(int(binding["HostPort"]))
            
            # Оставляем в памяти только те порты, которые реально заняты в Docker
            self.allocated_ports = docker_used_ports
        except Exception as e:
            print(f"[PortManager] Ошибка при синхронизации с Docker: {e}")

    def is_socket_open(self, port: int) -> bool:
        """Проверка, не занят ли порт сторонним приложением в ОС."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    def acquire_port(self) -> int | None:
        """Мгновенно находит и бронирует свободный порт в памяти."""
        for port in range(self.min_port, self.max_port + 1):
            if port not in self.allocated_ports and not self.is_socket_open(port):
                self.allocated_ports.add(port)  # Занимаем порт МГНОВЕННО
                return port
        return None

    def release_port(self, port: int):
        """Отмена бронирования (если контейнер упал при старте)."""
        self.allocated_ports.discard(port)


port_manager = PortManager(MIN_PORT, MAX_PORT)


def spawn_game_server(port: int):
    container_name = f"{CONTAINER_PREFIX}{port}"
    print(f"[Node Manager] Запускаем новый игровой сервер на порту {port}...")

    try:
        client.containers.run(
            image=DOCKER_IMAGE,
            name=container_name,
            detach=True,
            ports={f"{port}/tcp": port, f"{port}/udp": port},
            environment={
                "SERVER_PORT": str(port),
                "PUBLIC_IP": PUBLIC_IP,
                "MASTER_SERVER_URL": "http://host.docker.internal:8000",
            },
            auto_remove=True,
            mem_limit="512m",
            nano_cpus=1_000_000_000,  # лимит 1 CPU на контейнер
        )
        print(f"[Node Manager] Контейнер {container_name} успешно запущен!")
    except Exception as e:
        print(f"[Node Manager] Ошибка при запуске контейнера на порту {port}: {e}")
        # Если запуск свалился, только тогда снимаем бронь вручную
        port_manager.release_port(port)


def get_waiting_count() -> int:
    try:
        now = time.time()
        # 1. Вычищаем мёртвых
        r.zremrangebyscore(SERVERS_AVAILABLE_KEY, 0, now)
        # 2. Считаем живых в ZSET
        return r.zcard(SERVERS_AVAILABLE_KEY)
    except Exception as e:
        print(f"[Node Manager] Не удалось прочитать {SERVERS_AVAILABLE_KEY}: {e}")
        return WARM_POOL_SIZE


def main_loop():
    print(f"[Node Manager] Запущен! Целевой размер тёплого пула: {WARM_POOL_SIZE} свободных серверов.")

    while True:
        try:
            # 1. Синхронизируем порты в памяти с Docker (удаляем порты закрывшихся серверов)
            port_manager.sync_with_docker(client, CONTAINER_PREFIX)

            waiting_count = get_waiting_count()

            if waiting_count < WARM_POOL_SIZE:
                needed = WARM_POOL_SIZE - waiting_count
                print(f"[Node Manager] Свободных серверов: {waiting_count}/{WARM_POOL_SIZE}. Нужно поднять: {needed}")

                for _ in range(needed):
                    # 2. Выделяем порт мгновенно из памяти
                    port = port_manager.acquire_port()
                    if port:
                        spawn_game_server(port)
                    else:
                        print("[Node Manager] Закончились свободные порты в диапазоне!")
                        break
            else:
                print(f"[Node Manager] Свободных серверов: {waiting_count}/{WARM_POOL_SIZE}. Всё в порядке.")
        except Exception as e:
            print(f"[Node Manager Error] {e}")



if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n[Node Manager] Завершение работы по сигналу пользователя.")
        time.sleep(1)
        print("[Node Manager] Останавливаем все игровые серверы...")
        os.system("cls" if os.name == "nt" else "clear")
        os._exit(0)  # принудительно завершаем все фоновые таски, чтобы не висели в фоне