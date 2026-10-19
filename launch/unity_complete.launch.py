#!/usr/bin/env python3
#
# Launches:
#  - RViz2 with sim time and package rviz config
#  - ROS-TCP-Endpoint binding to 0.0.0.0
#  - Unity simulation bootstrap script (utils/load_usd_and_run.py)
#  - Activation of N robots through /robot_1/on ... /robot_N/on once ready
#
# Args:
#   run_rviz      (bool)  : launch RViz2 (default: true)
#   robot_count   (int)   : number of canonical RBWatchers [1..3] (default: 1)
#   world         (str)   : name of the world to load (default: empty_world)
#   headless      (bool)  : launch Unity in batchmode (default: false)
#   render_fps    (int)   : Unity render target, 1..1000 (default: env/60)

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    ExecuteProcess,
    GroupAction,
    OpaqueFunction,
    RegisterEventHandler,
)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


BOOLEAN_VALUES = {'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'}
TRUE_VALUES = {'true', '1', 'yes', 'on'}
WORLD_ARCHIVES = {
    'empty_world': 'unity_simulation_only.tar.gz',
    'simple_world': 'unity_simulation.tar.gz',
}


def archive_for_world(world: str) -> str:
    """Return the only runtime archive allowed for a launch world."""
    try:
        return WORLD_ARCHIVES[world]
    except KeyError as exc:
        raise ValueError(f'unsupported Unity world: {world!r}') from exc


def generate_launch_description():
    # -------------------------
    # Launch arguments
    # -------------------------
    run_rviz_arg = DeclareLaunchArgument(
        'run_rviz',
        default_value='true',
        description='Launch one RViz2 instance per requested robot',
    )

    robot_count_arg = DeclareLaunchArgument(
        'robot_count',
        default_value='1',
        description='Number of canonical RBWatchers to activate [1..3]',
    )

    world_arg = DeclareLaunchArgument(
        'world',
        default_value='empty_world',
        choices=['empty_world', 'simple_world'],
        description='Name of the world to load',
    )

    headless_arg = DeclareLaunchArgument(
        'headless',
        default_value='false',
        description='Launch Unity in batchmode while retaining sensor graphics',
    )

    render_fps_arg = DeclareLaunchArgument(
        'render_fps',
        default_value=os.environ.get('ROBOTNIK_RENDER_FPS', '60'),
        description='Unity render FPS target for benchmark runs [1..1000]',
    )

    robot_transport_arg = DeclareLaunchArgument(
        'robot_transport',
        default_value=os.environ.get(
            'ROBOTNIK_ROBOT_TRANSPORT', 'per_robot_transport'
        ),
        choices=['legacy', 'per_robot_transport'],
        description='Route robot-owned ROS traffic through one ROS-TCP endpoint per robot',
    )

    # -------------------------
    # Resolve and validate values that determine the launched processes.
    # -------------------------
    def launch_unity_with_world(context, *args, **kwargs):
        del args, kwargs
        selected_world_name = LaunchConfiguration('world').perform(context)
        world_filename = archive_for_world(selected_world_name)

        headless_value = LaunchConfiguration('headless').perform(context)
        headless_value = headless_value.strip().lower()
        if headless_value not in BOOLEAN_VALUES:
            raise ValueError(
                f'headless must be a boolean, got: {headless_value!r}'
            )
        is_headless = headless_value in TRUE_VALUES
        render_fps = LaunchConfiguration('render_fps').perform(context)
        try:
            render_fps_value = int(render_fps)
        except (TypeError, ValueError) as exc:
            raise ValueError('render_fps must be an integer between 1 and 1000') from exc
        if not 1 <= render_fps_value <= 1000:
            raise ValueError('render_fps must be between 1 and 1000')
        robot_transport = LaunchConfiguration('robot_transport').perform(context)

        try:
            robot_count = int(
                LaunchConfiguration('robot_count').perform(context)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                'robot_count must be an integer between 1 and 3'
            ) from exc
        if not 1 <= robot_count <= 3:
            raise ValueError('robot_count must be between 1 and 3')

        print(
            f'[INFO] Selected world: {selected_world_name!r} '
            f'-> using file: {world_filename!r}'
        )
        print(f'[INFO] Headless mode: {is_headless}')

        pkg_share_path = get_package_share_directory('unity_sim')
        autorun_script_path = os.path.join(
            pkg_share_path, 'utils', 'load_usd_and_run.py'
        )
        activator_path = os.path.join(
            pkg_share_path, 'utils', 'activate_robots.py'
        )

        # Build command with or without --batchmode
        cmd = [
            'python3',
            autorun_script_path,
            world_filename,
            '--render-fps',
            render_fps,
        ]
        if is_headless:
            cmd.append('--batchmode')

        unity_bootstrap = ExecuteProcess(
            cmd=cmd,
            name='unity_sim_bootstrap',
            output='screen',
            additional_env={'ROBOTNIK_ROBOT_TRANSPORT': robot_transport},
        )

        # Instantiate only the endpoints that the selected run can use.  The
        # previous launch always started ports 10001..10003 while leaving the
        # Player in legacy mode, so benchmark metadata appeared sharded even
        # though every image still crossed the common port 10000.
        robot_endpoints = []
        if robot_transport == 'per_robot_transport':
            robot_endpoints = [
                Node(
                    package='ros_tcp_endpoint',
                    executable='default_server_endpoint',
                    name=f'ros_tcp_endpoint_robot_{index}',
                    output='screen',
                    parameters=[
                        {'ROS_IP': '0.0.0.0'},
                        {'ROS_TCP_PORT': 10000 + index},
                    ],
                )
                for index in range(1, robot_count + 1)
            ]

        unity_exit_handler = RegisterEventHandler(
            OnProcessExit(
                target_action=unity_bootstrap,
                on_exit=[
                    EmitEvent(event=Shutdown(reason='Unity simulation exited'))
                ],
            )
        )

        activator = ExecuteProcess(
            cmd=[
                'python3',
                activator_path,
                '--robot-count',
                str(robot_count),
            ],
            name='unity_robot_activator',
            output='screen',
        )

        def stop_on_activation_failure(event, context):
            del context
            if event.returncode == 0:
                return []
            return [
                EmitEvent(
                    event=Shutdown(
                        reason=(
                            'Unity robot activation failed with code '
                            f'{event.returncode}'
                        )
                    )
                )
            ]

        activator_exit_handler = RegisterEventHandler(
            OnProcessExit(
                target_action=activator,
                on_exit=stop_on_activation_failure,
            )
        )

        rviz_processes = []
        run_rviz_value = LaunchConfiguration('run_rviz').perform(context)
        run_rviz_value = run_rviz_value.strip().lower()
        if run_rviz_value not in BOOLEAN_VALUES:
            raise ValueError(
                f'run_rviz must be a boolean, got: {run_rviz_value!r}'
            )
        if run_rviz_value in TRUE_VALUES:
            for index in range(1, robot_count + 1):
                robot_name = 'robot' if index == 1 else f'robot_{index}'
                rviz_config = os.path.join(
                    pkg_share_path, 'rviz', f'{robot_name}.rviz'
                )
                rviz_processes.append(
                    Node(
                        package='rviz2',
                        executable='rviz2',
                        name=f'rviz2_{robot_name}',
                        arguments=['-d', rviz_config],
                        output='screen',
                        parameters=[{'use_sim_time': True}],
                    )
                )

        return [
            *robot_endpoints,
            unity_bootstrap,
            unity_exit_handler,
            activator,
            activator_exit_handler,
            *rviz_processes,
        ]

    # -------------------------
    # ROS–TCP–Endpoint (listens on 0.0.0.0)
    # -------------------------
    ros_tcp_endpoint = Node(
        package='ros_tcp_endpoint',
        executable='default_server_endpoint',
        name='ros_tcp_endpoint',
        output='screen',
        parameters=[{'ROS_IP': '0.0.0.0'}],
    )

    # -------------------------
    # Group and LD
    # -------------------------
    group = GroupAction([
        ros_tcp_endpoint,
        OpaqueFunction(function=launch_unity_with_world),
    ])

    return LaunchDescription([
        run_rviz_arg,
        robot_count_arg,
        world_arg,
        headless_arg,
        render_fps_arg,
        robot_transport_arg,
        group,
    ])
