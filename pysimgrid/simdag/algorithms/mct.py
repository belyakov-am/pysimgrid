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


from .. import scheduler
from ... import csimdag


class MCTScheduler(scheduler.DynamicScheduler):
  def prepare(self, simulation):
    for h in simulation.hosts:
      h.data = {}
      h.data["est"] = 0.
    self.queue = []

  def schedule(self, simulation, changed):
    for h in simulation.hosts:
      h.data["free"] = True
    for task in simulation.tasks[csimdag.TaskState.TASK_STATE_RUNNING, csimdag.TaskState.TASK_STATE_SCHEDULED]:
      task.hosts[0].data["free"] = False
    queue_set = set(self.queue)
    for t in simulation.tasks[csimdag.TaskState.TASK_STATE_SCHEDULABLE]:
      if t not in queue_set:
        self.queue.append(t)
    clock = simulation.clock
    while self.queue:
      free_hosts = simulation.hosts.by_data("free", True)
      if free_hosts:
        t = self.queue.pop(0)
        free_hosts = free_hosts.sorted(lambda h: self.get_ect(clock, t, h))
        target_host = free_hosts[0]
        t.schedule(target_host)
        target_host.data["free"] = False
        self._log.debug("%.3f: Scheduling %s to %s (old est: %.3f, new est: %.3f)", simulation.clock, t.name, target_host.name, target_host.data["est"], self.get_ect(clock, t, target_host))
        target_host.data["est"] = self.get_ect(clock, t, target_host)
      else:
        break

  @staticmethod
  def get_ect(clock, task, host):
    parent_connections = [p for p in task.parents if p.kind == csimdag.TaskKind.TASK_KIND_COMM_E2E]
    comm_times = [conn.get_ecomt(conn.parents[0].hosts[0], host) for conn in parent_connections]
    return max(host.data["est"], clock) + task.get_eet(host) + (max(comm_times) if comm_times else 0.)
