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


class OLBScheduler(scheduler.DynamicScheduler):
  def prepare(self, simulation):
    for h in simulation.hosts:
      h.data = {}
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
        free_hosts = free_hosts.sorted(lambda h: h.speed, reverse=True)
        target_host = free_hosts[0]
        t.schedule(target_host)
        target_host.data["free"] = False
      else:
        break