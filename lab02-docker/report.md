# Lab 02 — Running a Linux Container with Docker

## Summary

This lab covered installing Docker Desktop on Windows 10 (using WSL 2 as the backend), verifying the installation, pulling and running a Ubuntu Linux container interactively, and practicing the container lifecycle (start, stop, remove).

## Environment

 Host OS: Windows 10 (64-bit)
 WSL: WSL 2 with Ubuntu 26.04 LTS (host distribution)
 Docker Desktop: v4.89.0 (Personal plan)
 Container image: `ubuntu:22.04` (Jammy Jellyfish)

## Docker Image vs Docker Container

A Docker image is like a blueprint or recipe that contains everything needed to run a Linux environment or application, such as the operating system files, code, libraries, and default settings. A Docker container is a running (or stopped) instance created from that image. In simple terms, the image is the template, while the container is the actual environment running from it. For example, in this lab I pulled the `ubuntu:22.04` image once, but I could create many containers from it — each with its own filesystem changes, processes, and lifecycle. When I removed the container with `docker rm my-ubuntu`, the image itself remained on my machine, ready to spawn new containers.