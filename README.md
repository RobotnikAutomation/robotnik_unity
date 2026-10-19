# Robotnik Unity - Simulation

> **Work in Progress**
>
> This repository is currently maintained for evaluation and benchmarking
> purposes. It is not yet a complete production Robotnik simulation stack.

## Overview

The `unity_sim` ROS 2 package uses the Unity Engine to simulate Robotnik
robots, sensor data and world interactions. Its current purpose is to measure
Unity performance and compare it with Gazebo, Webots, Isaac Sim, O3DE and
MuJoCo.

The package currently provides:

- GUI and headless benchmark execution;
- one, two or three RB-Watcher robots;
- validated Unity runtime archives;
- configurable render FPS;
- optional RViz2;
- ROS-TCP communication through the official `ROS-TCP-Endpoint`.

The repository is named `robotnik_unity`, while the ROS 2 package is named
`unity_sim` for compatibility with the benchmark configuration.
