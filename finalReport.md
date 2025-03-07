# Falls Game

- [Jiahao Guo](mailto:jiahao.guo@studio.unibo.it)


### AI Disclaimer (if needed)

```
"During the preparation of this work, the author(s) used [NAME TOOL /
SERVICE] to [REASON].
After using this tool/service, the author(s) reviewed and edited the
content as needed and take(s) full responsibility for the content of the
final report/artifact."
```

## Abstract

### **Project Description, Goals, and Achievements**  

**Falls Game** is a multiplayer game inspired by one of the levels in *Fall Guys*. It features a multi-layered arena where players compete to be the last one standing as platforms disappear beneath them. Players can jump, dash, and interact with special blocks that grant temporary movement abilities, making the gameplay more dynamic and strategic.  

#### **Goals:**  
- Develop an engaging multiplayer experience with both online and local play.  
- Implement smooth and responsive movement mechanics, including jumping and dashing.  
- Design a robust networking system using WebSockets, TCP/UDP, and P2P connections.  
- Ensure synchronization between players for a fair and competitive experience.  

#### **Achievements:**  
- Successfully built the game using Python and the Ursina framework for rendering.  
- Implemented online multiplayer with a client-server model and matchmaking server.  
- Developed a local multiplayer mode with P2P connectivity for seamless LAN play.  
- Created a variety of interactive platform mechanics to enhance strategic gameplay.  

The project combines technical innovation with fun, competitive gameplay, making *Falls Game* an exciting multiplayer experience.

## Concept

#### Type of Product
Falls Game is a multiplayer desktop application developed using the Python Ursina framework for game development and rendering. It offers both online and local multiplayer modes, allowing players to compete in real-time as they navigate disappearing platforms in a dynamic arena.

#### User Interaction Context
##### Where are the users?
Players can be located anywhere with an internet connection for online matchmaking. For local multiplayer, players must be on the same network.

##### When and how frequently do they interact with the system?
Users interact with the game whenever they launch it, typically for casual gameplay sessions.

##### How do users interact with the system?
Players use a keyboard and mouse/game controller to move, jump, and dash within the game.
A GUI-based menu allows them to select online matchmaking and local play.

##### Which devices are they using?
The game is designed primarily for PC.

#### Data Storage & Management
##### Does the system store user data?
No persistent data storage is used at this stage.
Redis is used as a temporary caching solution to store player session data.
If a player disconnects, Redis retains their session data for a short time, allowing them to reconnect without losing progress.
Once a match ends, all session data is cleared from Redis.

#### User Roles
##### Players:
The main users who join matches and compete in the game.

##### Matchmaking & Logic Server:
Handles player connections, manages temporary session data in Redis, and synchronizes game states.

##### Local Host:
In local multiplayer, players are connected via p2p.

## Requirements

- The requirements must explain __what__ (not how) the software being produced should do. 
    * you should not focus on the particular problems, but exclusively on what you want the application to do.

- Requirements must be clearly identified, and possibly numbered

- Requirements are divided into:
    - **Functional**: some functionality the software should provide to the user
    - **Non-functional**: requirements that do not directly concern behavioural aspects, such as consistency, availability, etc.
    - **Implementation**: constrain the entire phase of system realization, for instance by requiring the use of a specific programming language and/or a specific software tool
        + these constraints should be adequately justified by political / economic / administrative reasons...
        + ... otherwise, implementation choices should emerge _as a consequence of_ design

- If there are domain-specific terms, these should be explained in a glossary

- Each requirement must have its own __acceptance criteria__
    + these will be important for the validation phase

## Design

This chapter explains the strategies used to meet the requirements identified in the analysis.
Ideally, the design should be the same, regardless of the technological choices made during the implementation phase.

> You can re-order the sections as you prefer, but all the sections must be present in the end

### Architecture

- Which architectural style? 
    + why?

### Infrastructure

- are there _infrastructural components_ that need to be introduced? _how many_?
    * e.g. _clients_, _servers_, _load balancers_, _caches_, _databases_, _message brokers_, _queues_, _workers_, _proxies_, _firewalls_, _CDNs_, _etc._

- how do components	_distribute_ over the network? _where_?
    * e.g. do servers / brokers / databases / etc. sit on the same machine? on the same network? on the same datacenter? on the same continent?

- how do components _find_ each other?
    * how to _name_ components?
    * e.g. DNS, _service discovery_, _load balancing_, _etc._

> Component diagrams are welcome here

### Modelling

- which __domain entities__ are there?
    * e.g. _users_, _products_, _orders_, _etc._

- how do _domain entities_ __map to__ _infrastructural components_?
    * e.g. state of a video game on central server, while inputs/representations on clients
    * e.g. where to store messages in an IM app? for how long?

- which __domain events__ are there?
    * e.g. _user registered_, _product added to cart_, _order placed_, _etc._

- which sorts of __messages__ are exchanged?
    * e.g. _commands_, _events_, _queries_, _etc._

- what information does the __state__ of the system comprehend
    * e.g. _users' data_, _products' data_, _orders' data_, _etc._

> Class diagram are welcome here

### Interaction

- how do components _communicate_? _when_? _what_? 
- _which_ __interaction patterns__ do they enact?

> Sequence diagrams are welcome here

### Behaviour

- how does _each_ component __behave__ individually (e.g. in _response_ to _events_ or messages)?
    * some components may be _stateful_, others _stateless_

- which components are in charge of updating the __state__ of the system? _when_? _how_?

> State diagrams are welcome here

### Data and Consistency Issues

- Is there any data that needs to be stored?
    * _what_ data? _where_? _why_?

- how should _persistent data_ be __stored__?
    * e.g. relations, documents, key-value, graph, etc.
    * why?

- Which components perform queries on the database?
    * _when_? _which_ queries? _why_?
    * concurrent read? concurrent write? why?

- Is there any data that needs to be shared between components?
    * _why_? _what_ data?

### Fault-Tolerance

- Is there any form of data __replication__ / federation / sharing?
    * _why_? _how_ does it work?

- Is there any __heart-beating__, __timeout__, __retry mechanism__?
    * _why_? _among_ which components? _how_ does it work?

- Is there any form of __error handling__?
    * _what_ happens when a component fails? _why_? _how_?

### Availability

- Is there any __caching__ mechanism?
    * _where_? _why_?

- Is there any form of __load balancing__?
    * _where_? _why_?

- In case of __network partitioning__, how does the system behave?
    * _why_? _how_?

### Security

- Is there any form of __authentication__?
    * _where_? _why_?

- Is there any form of __authorization__?
    * which sort of _access control_?
    * which sorts of users / _roles_? which _access rights_?

- Are __cryptographic schemas__ being used?
    * e.g. token verification, 
    * e.g. data encryption, etc.

--- 
<!-- Riparti da qui  -->

## Implementation

- which __network protocols__ to use?
    * e.g. UDP, TCP, HTTP, WebSockets, gRPC, XMPP, AMQP, MQTT, etc.
- how should _in-transit data_ be __represented__?
    * e.g. JSON, XML, YAML, Protocol Buffers, etc.
- how should _databases_ be __queried__?
    * e.g. SQL, NoSQL, etc.
- how should components be _authenticated_?
    * e.g. OAuth, JWT, etc.
- how should components be _authorized_?
    * e.g. RBAC, ABAC, etc.

### Technological details

- any particular _framework_ / _technology_ being exploited goes here

## Validation

### Automatic Testing

- how were individual components **_unit_-test**ed?
- how was communication, interaction, and/or integration among components tested?
- how to **_end-to-end_-test** the system?
    * e.g. production vs. test environment

- for each test specify:
    * rationale of individual tests
    * how were the test automated
    * how to run them
    * which requirement they are testing, if any

> recall that _deployment_ __automation__ is commonly used to _test_ the system in _production-like_ environment

> recall to test corner cases (crashes, errors, etc.)

### Acceptance test

- did you perform any _manual_ testing?
    * what did you test?
    * why wasn't it automatic?


## Release

- how where components organized into _inter-dependant modules_ or just a single monolith?
    * provide a _dependency graph_ if possible

- were modules distributed as a _single archive_ or _multiple ones_?
    * why?

- how were archive versioned? 

- were archive _released_ onto some archive repository (e.g. Maven, PyPI, npm, etc.)?
    * how to _install_ them?

## Deployment

- should one install your software from scratch, how to do it?
    * provide instructions
    * provide expected outcomes

## User Guide

- how to use your software?
    * provide instructions
    * provide expected outcomes
    * provide screenshots if possible


## Self-evaluation

- An individual section is required for each member of the group
- Each member must self-evaluate their work, listing the strengths and weaknesses of the product
- Each member must describe their role within the group as objectively as possible. 
It should be noted that each student is only responsible for their own section