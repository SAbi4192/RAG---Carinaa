# Cloud Computing and Virtualization — Course Notes

## 1. Introduction to Cloud Computing

Cloud computing is the delivery of computing services — servers, storage,
databases, networking, software and analytics — over the internet, on demand and
on a pay-as-you-go basis. The essential idea is that the user no longer owns or
maintains the physical hardware; they rent capacity from a provider and pay only
for what they consume.

The United States National Institute of Standards and Technology (NIST) defines
cloud computing through five essential characteristics: on-demand self-service,
broad network access, resource pooling, rapid elasticity, and measured service.

## 2. Virtualization

Virtualization is the foundational technology that makes cloud computing
economically viable. It is the creation of a virtual — rather than physical —
version of a computing resource, such as a server, a storage device, or a network.

A hypervisor, also called a Virtual Machine Monitor (VMM), is the software layer
that creates and runs virtual machines. It sits between the physical hardware and
the guest operating systems and allocates CPU time, memory and I/O to each virtual
machine.

Virtualization improves resource utilization dramatically. A physical server that
would otherwise run at 10 to 15 percent utilization can host many virtual machines
and reach 70 to 80 percent utilization. This consolidation is the primary economic
driver behind cloud computing.

There are two types of hypervisors:

- Type 1 (bare-metal) hypervisors run directly on the host's hardware. Examples
  include VMware ESXi, Microsoft Hyper-V and KVM.
- Type 2 (hosted) hypervisors run as a software layer on top of an operating
  system. Examples include Oracle VirtualBox and VMware Workstation.

## 3. Containers

Containers are a lighter-weight form of virtualization. Unlike a virtual machine,
a container does not bundle a full guest operating system. Instead, containers
share the host operating system kernel and isolate only the application and its
dependencies.

Because there is no guest OS to boot, a container typically starts in under one
second, whereas a virtual machine may take 30 to 60 seconds. Containers are also
much smaller: a container image is often tens of megabytes, while a virtual
machine image is frequently several gigabytes.

Docker is the most widely used container platform. Kubernetes is the standard
orchestration system for managing containers at scale across a cluster.

## 4. Service Models

Cloud services are commonly divided into three models:

- Infrastructure as a Service (IaaS) provides virtualized computing resources
  over the internet. The customer manages the operating system and applications.
  Examples: Amazon EC2, Google Compute Engine.
- Platform as a Service (PaaS) provides a managed environment for deploying
  applications without managing the underlying infrastructure. Examples: Google
  App Engine, Heroku.
- Software as a Service (SaaS) delivers complete applications over the internet.
  The customer simply uses the software. Examples: Gmail, Salesforce, Microsoft 365.

## 5. Deployment Models

- Public cloud: infrastructure is owned by a third-party provider and shared
  among many customers over the internet.
- Private cloud: infrastructure is operated exclusively for a single
  organization.
- Hybrid cloud: a combination of public and private, connected so that data and
  applications can move between them.
- Community cloud: shared infrastructure for a specific community with common
  concerns.

## 6. Advantages and Limitations

The principal advantages of cloud computing are cost efficiency, elasticity,
global reach, automatic software updates, and reduced capital expenditure.

The principal limitations are dependence on network connectivity, vendor lock-in,
data sovereignty and compliance concerns, and reduced direct control over the
underlying infrastructure.

## 7. Summary

Virtualization abstracts physical hardware so that many logical machines can share
one physical host. That abstraction is what allows cloud providers to pool
resources, sell them in small increments, and scale elastically. Containers extend
the same principle to the application layer with far less overhead. Together,
virtualization and containerization are the technical foundation on which every
cloud service model is built.
