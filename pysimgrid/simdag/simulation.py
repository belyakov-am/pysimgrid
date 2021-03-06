# This file is part of pysimgrid, a Python interface to the SimGrid library.
#
# Copyright 2015-2016 Alexey Nazarenko and contributors
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# along with this library.  If not, see <http://www.gnu.org/licenses/>.
#

import collections
import logging
import networkx
import operator
import os

from .scheduler import TaskExecutionMode
from .. import csimdag
from .. import tools

class Simulation(object):
  """
  High-level API for current simulation state.

  * Ensures proper bootstrap/cleanup
  * Provides an improved API for task/host filtering.
  * Ensures that you always use exactly same python instances for SimGrid objects

    * e.g csimdag.Task.parents returns brand-new Task instances you've never seen before
    * required
    * required for (Task/Host).data to work properly

  """

  _INSTANCE = None
  _DEFAULT_CONFIG = {
    "network/model": "LV08"
  }

  def __init__(self, platform, tasks, estimator=tools.AccurateEstimator(), config=None, log_config=None):
    self._platform_src = platform
    self._tasks_src = tasks
    self._estimator = estimator
    self._config = self._DEFAULT_CONFIG
    self._log_config = log_config
    if config:
      assert isinstance(config, dict)
      self._config.update(config)
    self._hosts = None
    self._tasks = None
    self._logger = logging.getLogger("simdag.Simulation")
    if not os.path.isfile(self._platform_src):
      raise IOError("platform definition file {} does not exist".format(self._platform_src))
    if not os.path.isfile(self._tasks_src):
      raise IOError("tasks definition file {} does not exist".format(self._tasks_src))

  def simulate(self, how_long=-1.):
    """
    Run the simgrid simulation until one of the following happens:
    
    * how_long time limit expires (if passed and positive)
    * watchpoint is reached (some task changed state)
    * simulation ends

    Returns the list of changed tasks.
    """
    changed = csimdag.simulate(how_long)
    changed_ids = [t.native for t in changed]
    changed_tasks = _TaskList([t for t in self._tasks if t.native in changed_ids])

    self._logger.debug("%.6f ------------------------------------------------------------------" % self.clock)
    for task in changed_tasks:
        if task.kind == csimdag.TASK_KIND_COMP_SEQ:
            if task.state == csimdag.TASK_STATE_DONE:
                self._logger.debug("%20s: %s (%s, %.6f - %.6f)" %
                                   (task.name, str(task.state), task.hosts[0].name, task.start_time, task.finish_time))
            else:
                self._logger.debug("%20s: %s (%s, %.6f)" %
                                   (task.name, str(task.state), task.hosts[0].name, task.start_time))
        else:
            if task.state == csimdag.TASK_STATE_DONE:
                self._logger.debug("%20s: %s (%s - %s, %.6f - %.6f)" %
                                   (task.name, str(task.state), task.hosts[0].name,
                                    (task.hosts[1].name if len(task.hosts) == 2 else task.hosts[0].name),
                                    task.start_time, task.finish_time))
            else:
                self._logger.debug("%20s: %s (%s - %s, %.6f)" %
                                   (task.name, str(task.state), task.hosts[0].name,
                                    (task.hosts[1].name if len(task.hosts) == 2 else task.hosts[0].name),
                                    task.start_time))

    return changed_tasks

  def get_task_graph(self):
    """
    Get current DAG as a nxgraph.DiGraph.

    Task computation/communication amounts are represented as an "weight" attribute of nodes and edges.
    """
    free_tasks = self.tasks.by_func(lambda t: not t.parents)
    if len(free_tasks) != 1:
      raise Exception("cannot find DAG root")

    graph = networkx.DiGraph()
    for t in self.tasks:
      graph.add_node(t, weight=t.amount)

    for e in self.connections:
      parents, children = e.parents, e.children
      assert len(parents) == 1 and len(children) == 1
      # make sure that the original task graph is not multigraph!
      assert not graph.has_edge(parents[0], children[0])
      graph.add_edge(parents[0], children[0], weight=e.amount)

    return graph

  @property
  def tasks(self):
    """
    Get all computational tasks.
    """
    return self.all_tasks.by_prop("kind", csimdag.TASK_KIND_COMM_E2E, True)

  @property
  def connections(self):
    """
    Get all communication tasks.
    """
    return self.all_tasks.by_prop("kind", csimdag.TASK_KIND_COMM_E2E)

  @property
  def all_tasks(self):
    """
    Get full task list, including comm tasks.
    """
    return _TaskList(self._tasks)

  @property
  def hosts(self):
    """
    Get full host list.
    """
    return _InstanceList(self._hosts)

  @property
  def platform_path(self):
    """
    Get path to platform definition file.
    """
    return self._platform_src

  @property
  def clock(self):
    """
    Get current SimGrid clock.
    """
    return csimdag.get_clock()

  def add_dependency(self, src_task, dst_task):
    """
    Add dependency between given tasks, if not already exists.
    """
    csimdag.add_dependency(src_task, dst_task)

  def add_task(self, name, amount):
    """
    Add computational task.
    """
    task = csimdag.add_task(name, amount)
    sim_task = _SimulationTask(task.native, self, self._logger)
    self._tasks.append(sim_task)
    return sim_task

  def sanity_check(self):
    """
    Check whether task executions overlap on hosts or not.
    """
    timetable_per_host = {}
    for task in self.tasks:
      host = task.hosts
      assert(len(host) == 1)
      if host[0].name not in timetable_per_host:
        timetable_per_host[host[0].name] = []
      timetable_per_host[host[0].name].append((task.start_time, task.finish_time))
    for host in timetable_per_host:
      last_ended = -1
      for task_time in sorted(timetable_per_host[host], key=operator.itemgetter(0)):
        if task_time[0] < last_ended:
          return False
        last_ended = task_time[1]
    return True

  def __enter__(self):
    """
    Context interface implementation.
    """
    if self._INSTANCE is not None:
      raise Exception("Simulation may be used only once per process (SimGrid currently does not support reinitialization)")

    self._logger.debug("Initialization started")
    csimdag.initialize()

    if self._log_config:
      self._logger.debug("Setting XBT log configuration")
      csimdag.log_config(self._log_config)

    self._logger.debug("Setting configuration parameters")
    for k, v in self._config.items():
      self._logger.debug("  %s = %s", k, v)
      csimdag.config(k, v)


    self._logger.debug("Loading platform definition (source: %s)", self._platform_src)
    self._hosts = csimdag.load_platform(self._platform_src)
    self._logger.debug("Platform loaded, %d hosts", len(self._hosts))

    self._logger.debug("Loading task definition (source: %s)", self._tasks_src)
    tasks = csimdag.load_tasks(self._tasks_src)
    self._tasks = [_SimulationTask(t.native, self, self._logger) for t in tasks]
    comm_tasks_count = len(self.connections)
    self._logger.debug("Tasks loaded, %d nodes, %d links", len(self._tasks) - comm_tasks_count, comm_tasks_count)

    if self._estimator is not None:
      for task in self.tasks:
        if task.amount > 0:
          task.amount_estimate = self._estimator.generate(task.amount)
      self._logger.debug("Generated estimates using provided estimator")

    self._logger.debug("Simulation initialized")
    Simulation._INSTANCE = self
    return self

  def __exit__(self, *args):
    """
    Context interface implementation.
    """
    self._logger.debug("Finalizing the simulation (clock: %.2f)", self.clock)

    # perform sanity check of produced execution
    if "PYSIMGRID_TASK_EXECUTION" in os.environ:
      task_exec_mode = TaskExecutionMode[os.environ["PYSIMGRID_TASK_EXECUTION"]]
    else:
      task_exec_mode = TaskExecutionMode.SEQUENTIAL
    if task_exec_mode != TaskExecutionMode.PARALLEL:
      if self.sanity_check():
        self._logger.debug("Sanity check PASSED")
      else:
        raise Exception("Sanity check FAILED (task executions overlap on hosts!)")
    else:
      self._logger.debug("Sanity check SKIPPED (task execution mode is PARALLEL)")

    csimdag.exit()
    return False


class _SimulationTask(csimdag.Task):
  """
  Supporting class - wrap csimdag.Task methods that return new Task/Host instances.
  """
  def __init__(self, native, simulation, logger):
    self.native = native
    self._sim = simulation
    self._logger = logger

  @property
  def hosts(self):
    return self.__remap(super(_SimulationTask, self).hosts, self._sim.hosts)

  @property
  def children(self):
    return self.__remap(super(_SimulationTask, self).children, self._sim.all_tasks)

  @property
  def parents(self):
    return self.__remap(super(_SimulationTask, self).parents, self._sim.all_tasks)

  def schedule(self, host):
    self._logger.debug("Scheduling task '%s' to host '%s'", self.name, host.name)
    super(_SimulationTask, self).schedule(host)

  def schedule_after(self, host, predecessor):
    self._logger.debug("Scheduling task '%s' to host '%s' after '%s'", self.name, host.name, predecessor.name)
    super(_SimulationTask, self).schedule_after(host, predecessor)

  def __remap(self, internal_list, public_list):
    """
    A bit ugly instance remapper. Strange implementation is to preserve original order (it may suddenly matter).
    """
    ids_order = {obj.native: idx for (idx, obj) in enumerate(internal_list)}
    ids_set = set(ids_order)
    return public_list.by_func(lambda p: p.native in ids_set).sorted(lambda el: ids_order[el.native])

  def __gt__(self, other):
    return self.name > other.name


class _InstanceList(object):
  """
  Object list wrapper to simplify common filtering.
  """
  def __init__(self, instances):
    self._list = instances

  def by_prop(self, property_name, value, negate=False):
    """
    Select instances by property value.
    """
    if negate:
      return type(self)([el for el in self._list if getattr(el, property_name) != value])
    return type(self)([el for el in self._list if getattr(el, property_name) == value])

  def by_data(self, key, *value):
    """
    Select instances by data attribute.

    If no 'value' arg is passed, condition is obj.data == key,
                                 else it is   obj.data[key] == value
    """
    if len(value) > 1:
      raise Exception("only single value can be passed")
    if value:
      return type(self)([el for el in self._list if el.data.get(key) == value[0]])
    return type(self)([el for el in self._list if el.data == key])

  def by_func(self, func):
    """
    Select instances by custom filter.
    """
    return type(self)([el for el in self._list if func(el)])

  def sorted(self, key, reverse=False):
    """
    Sort instance on custom criterion.
    """
    return type(self)(sorted(self._list, key=key, reverse=reverse))

  def __getitem__(self, arg):
    """
    Sequence interface implementation.
    """
    if isinstance(arg, int):
      return self._list[arg]
    elif isinstance(arg, slice):
      return type(self)(self._list[slice])
    else:
      raise TypeError("unsupported indexer type")

  def __len__(self):
    """
    Sequence interface implementation.
    """
    return len(self._list)

  def __contains__(self, element):
    """
    Sequence interface implementation.
    """
    return element in self._list

  def __iter__(self):
    """
    Sequence interface implementation.
    """
    return iter(self._list)

  def __str__(self):
    """
    Sequence interface implementation.
    """
    return str(self._list)


class _TaskList(_InstanceList):
  """
  Task list wrapper to simplify common filtering even more.
  """
  def __getitem__(self, arg):
    """
    Sequence interface implementation.
    """
    if isinstance(arg, collections.Sequence):
      values = [v for v in arg]
      if not values:
        return type(self)([])
      if len(set(map(type, values))) != 1:
        raise TypeError("sequence-based indexer must contain only one value type")
      arg0 = values[0]
      if isinstance(arg0, csimdag.TaskState):
        return self.by_func(lambda t: t.state in values)
      elif isinstance(arg0, csimdag.TaskKind):
        return self.by_func(lambda t: t.kind in values)
      else:
        raise TypeError("sequence-based indexing is supported only for TaskState and TaskKind lists")
    elif isinstance(arg, csimdag.TaskState):
      return self.by_prop("state", arg)
    elif isinstance(arg, csimdag.TaskKind):
      return self.by_prop("kind", arg)
    elif isinstance(arg, int):
      return self._list[arg]
    elif isinstance(arg, slice):
      return type(self)(self._list[slice])
    else:
      raise TypeError("unsupported indexer type")
