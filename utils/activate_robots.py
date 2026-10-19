#!/usr/bin/env python3
"""Wait for Unity robot services and activate the requested robots."""

import argparse
import time
from typing import Sequence

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger


def robot_service_names(robot_count: int) -> list[str]:
    if not 1 <= robot_count <= 3:
        raise ValueError('robot_count must be between 1 and 3')
    return [f'robot_{index}' for index in range(1, robot_count + 1)]


class RobotActivator(Node):
    def __init__(self, names: Sequence[str]) -> None:
        super().__init__('unity_robot_activator')
        self._activation_clients = [
            (name, self.create_client(Trigger, f'/{name}/on')) for name in names
        ]

    def activate(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec

        # Wait for every service before changing simulation state.  This avoids
        # partially activated scenarios if Unity has not registered all robots.
        for name, client in self._activation_clients:
            service_name = f'/{name}/on'
            self.get_logger().info(f'Waiting for {service_name}')
            while rclpy.ok() and not client.wait_for_service(timeout_sec=1.0):
                if time.monotonic() >= deadline:
                    self.get_logger().error(
                        f'Timed out waiting for {service_name} '
                        f'after {timeout_sec:.1f} s'
                    )
                    return False

        for name, client in self._activation_clients:
            service_name = f'/{name}/on'
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                self.get_logger().error(
                    f'Timed out before calling {service_name} '
                    f'after {timeout_sec:.1f} s'
                )
                return False

            future = client.call_async(Trigger.Request())
            rclpy.spin_until_future_complete(self, future, timeout_sec=remaining)
            if not future.done():
                self.get_logger().error(f'Timed out calling {service_name}')
                return False
            if future.exception() is not None:
                self.get_logger().error(
                    f'Call to {service_name} failed: {future.exception()}'
                )
                return False

            response = future.result()
            if response is None or not response.success:
                message = 'no response' if response is None else response.message
                self.get_logger().error(
                    f'Unity rejected activation through {service_name}: {message}'
                )
                return False
            self.get_logger().info(
                f'Activated {name} through {service_name}: {response.message}'
            )

        self.get_logger().info(
            f'Successfully activated exactly {len(self._activation_clients)} '
            'requested robot(s)'
        )
        return True


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--robot-count', required=True, type=int, choices=range(1, 4)
    )
    parser.add_argument('--timeout', type=float, default=120.0)
    parsed = parser.parse_args(args)
    if parsed.timeout <= 0.0:
        parser.error('--timeout must be positive')
    return parsed


def main(args: Sequence[str] | None = None) -> int:
    parsed = parse_args(args)
    rclpy.init()
    node = RobotActivator(robot_service_names(parsed.robot_count))
    try:
        return 0 if node.activate(parsed.timeout) else 1
    except KeyboardInterrupt:
        return 130
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
