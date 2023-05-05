import os
import glob
import hashlib
import subprocess
import yaml
import networkx as nx
import logging.handlers
from collections import defaultdict
import argparse
import shutil
import datetime
import pdb
import concurrent.futures
from queue import Queue
import threading

class BuildTool:
    def __init__(self, base_dir, cache_dir):
        self.base_dir = base_dir
        self.cache_dir = cache_dir
        self.setup_logging()

        # Create the cache directory if it does not exist
        os.makedirs(self.cache_dir, exist_ok=True)

        self.task_bld_files = {}  # Add this attribute to store the mapping between tasks and their .bld file paths


        bld_files = self.find_bld_files(base_dir)

        tasks = {}
        for bld_file in bld_files:
            tasks.update(self.parse_bld_file(bld_file))

        self.tasks = tasks

    def setup_logging(self):
        self.logger = logging.getLogger("BuildTool")
        self.logger.setLevel(logging.DEBUG)

        # Use a unique log file name based on the current timestamp
        current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_name = f"build_tool_{current_time}.log"

        log_file_handler = logging.handlers.RotatingFileHandler(log_file_name, maxBytes=10000000, backupCount=5)
        log_file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        log_file_handler.setFormatter(file_formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter("%(levelname)s: %(message)s")
        console_handler.setFormatter(console_formatter)

        self.logger.addHandler(log_file_handler)
        self.logger.addHandler(console_handler)

    def find_bld_files(self, base_dir):
        bld_files = []
        for root, _, files in os.walk(base_dir):
            for filename in files:
                if filename.endswith(".bld"):
                    bld_files.append(os.path.join(root, filename))
        return bld_files

    def parse_bld_file(self, filepath):
        with open(filepath, "r") as file:
            tasks = yaml.safe_load(file)

        bld_file_dir = os.path.dirname(filepath)
        for task_name, task in tasks.items():
            collectList = []
            for output in task["outputs"]:
                collectList.append(os.path.join(bld_file_dir, output))
            task["outputs"] = collectList
            self.task_bld_files[task_name] = filepath  # Store the .bld file path for each task
        return tasks

    def hash_file(self, filepath):
        with open(filepath, "rb") as f:
            file_data = f.read()
            return hashlib.sha1(file_data).hexdigest()

    def expand_glob_patterns(self, patterns, base_dir):
        expanded_files = set()
        for pattern in patterns:
            full_pattern = os.path.join(base_dir, pattern)
            glob_expanded = glob.glob(full_pattern, recursive=True)
            if glob_expanded:
                expanded_files.update(glob_expanded)
            elif os.path.isfile(full_pattern):
                expanded_files.add(full_pattern)
        return list(expanded_files)


    def get_task_cache_key(self, task):
        cache_key_data = {
            "name": task["name"],
            "command": task["command"],
            "dependencies": task.get("dependencies", []),
        }
        
        # Add hashes of dependencies to cache_key_data
        dependency_hashes = {}
        for dep_name in task.get("dependencies", []):
            dep_task = self.tasks[dep_name]
            dep_cache_key = self.get_task_cache_key(dep_task)
            dependency_hashes[dep_name] = dep_cache_key
        cache_key_data["dependency_hashes"] = dependency_hashes

        bld_file_dir = os.path.dirname(self.task_bld_files[task["name"]])
        
        if "inputs" in task:
            expanded_inputs = self.expand_glob_patterns(task["inputs"], bld_file_dir)
            inputs_hash = {input_file: self.hash_file(input_file) for input_file in expanded_inputs}
            cache_key_data["inputs"] = inputs_hash
        
        cache_key_str = yaml.dump(cache_key_data)
        return hashlib.sha1(cache_key_str.encode("utf-8")).hexdigest()

    def build_dag(self, tasks):
        dag = nx.DiGraph()
        for task_name, task in tasks.items():
            dag.add_node(task_name, task=task)
            for dependency in task.get("dependencies", []):
                if dependency not in tasks:
                    self.logger.error(f"Undefined dependency '{dependency}' in task '{task_name}'")
                    return
                dag.add_edge(dependency, task_name)
        return dag

    def get_task_order(self, dag, target_name):
        task_order = nx.algorithms.dag.topological_sort(dag)
        task_order = [task_name for task_name in task_order if task_name == target_name or nx.has_path(dag, task_name, target_name)]

        # Add all the tasks required to build the target, including their dependencies
        task_order_with_deps = []
        for task_name in task_order:
            task_deps = nx.algorithms.dag.ancestors(dag, task_name)
            task_order_with_deps.extend(task_deps)
            task_order_with_deps.append(task_name)
        task_order_with_deps = list(dict.fromkeys(task_order_with_deps))  # 

        return task_order_with_deps

    def execute_task(self, task):
        inputs = task.get("inputs", [])
        outputs = task.get("outputs", [])


        bld_file_dir = os.path.dirname(self.task_bld_files[task["name"]])

        # Check if all inputs and outputs exist
        missing_inputs = [input for input in inputs if not os.path.exists(os.path.join(os.path.dirname(self.task_bld_files[task["name"]]), input))]
        missing_outputs = [output for output in outputs if not os.path.exists(os.path.join(os.path.dirname(self.task_bld_files[task["name"]]), output))]
        if missing_inputs:
            self.logger.error(f"Missing inputs: {missing_inputs}")
            return
        if missing_outputs:
            self.logger.info(f"Creating missing outputs: {missing_outputs}")

        # Execute the command
        cmd = task.get("command")
        bld_file_dir = os.path.dirname(self.task_bld_files[task["name"]])
        self.logger.info(f"Executing task '{task['name']}' with command '{cmd}' in directory '{bld_file_dir}'...")
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=bld_file_dir)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            self.logger.error(f"Task '{task['name']}' failed with exit code {proc.returncode}.")
            self.logger.error(f"stdout: {stdout.decode().strip()}")
            self.logger.error(f"stderr: {stderr.decode().strip()}")
            return

        # Cache the output
        cache_key = self.get_task_cache_key(task)
        cache_folder_path = os.path.join(self.cache_dir, cache_key)

        if not os.path.exists(cache_folder_path):
            os.makedirs(cache_folder_path)

        output_cache_info = self.cache_task_output(task, cache_folder_path)

    def get_file_hash(file_path):
        with open(file_path, 'rb') as file:
            file_data = file.read()
            file_hash = hashlib.sha256(file_data).hexdigest()
        return file_hash


    def cache_task_output(self, task, cache_folder_path):
        outputs = task.get("outputs", [])
        output_cache_info = {}
        filestore_path = os.path.join(self.cache_dir, "filestore")
        if not os.path.exists(filestore_path):
            os.makedirs(filestore_path)

        # # Save the task name to a file in the cache folder
        # task_name_file_path = os.path.join(cache_folder_path, "task_name.txt")
        # with open(task_name_file_path, "w") as task_name_file:
        #     task_name_file.write(task["name"])

        bld_file_dir = os.path.dirname(self.task_bld_files[task["name"]])

        for output in outputs:
            output_path = os.path.join(bld_file_dir, output)
            output_hash = hashlib.sha256(open(output_path, 'rb').read()).hexdigest()
            cache_file_name = output_hash
            cache_file_path = os.path.join(filestore_path, cache_file_name)

            if not os.path.exists(cache_file_path):
                self.logger.debug(f"Copying output '{output_path}' to filestore '{cache_file_path}'...")
                shutil.copyfile(output_path, cache_file_path)
            else:
                self.logger.debug(f"Output '{output_hash}' already exists in filestore ")

            relative_output_path = os.path.relpath(output_path, bld_file_dir)
            task_file_path = os.path.join(cache_folder_path, relative_output_path)
            task_file_dir = os.path.dirname(task_file_path)
            if not os.path.exists(task_file_dir):
                os.makedirs(task_file_dir)
            with open(task_file_path, 'w') as task_file:
                task_file.write(output_hash)

            output_cache_info[output] = {"cache_file_name": cache_file_name, "output_path": output_path}

        return output_cache_info


    def get_task_levels(self, dag, task_order):
        task_levels = {}
        for task_name in task_order:
            level = 0
            for ancestor in nx.ancestors(dag, task_name):
                level = max(level, task_levels[ancestor] + 1)
            task_levels[task_name] = level
        return task_levels


    def reuse_cached_output(self, task, cache_metadata):
        task_key = self.get_task_cache_key(task)
        if task_key not in cache_metadata.keys():
            return False

        cached_task = cache_metadata[task_key]

        task["output_cache_info"] = cached_task["output_cache_info"]
        output_cache_info = task.get("output_cache_info", {})
        for curFile, cache_info in output_cache_info.items():
            cache_folder_path = os.path.join(self.cache_dir, task_key)
            relative_output_path = curFile
            cache_file_path = os.path.join(cache_folder_path, relative_output_path)

            if os.path.exists(cache_file_path):
                self.logger.info(f"Using cached output file for task '{task['name']}' and output '{cache_info['output_path']}'...")


                # Create output directory if necessary
                output_dir = os.path.dirname(cache_info["output_path"])
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)

                # Fetch file content from the file store based on the hash of the file
                file_store_path = os.path.join(self.cache_dir, "filestore", cache_info['cache_file_name'])
                with open(file_store_path, "rb") as file_store_file:
                    file_content = file_store_file.read()

                with open(cache_info["output_path"], "wb") as output_file:
                    output_file.write(file_content)
            else:
                self.logger.error(f"Unable to find cached output file for task '{task['name']}' and output '{cache_info['output_path']}'.")
                return False

        return True


    def execute_tasks(self, target_name, tasks):
        dag = self.build_dag(tasks)

        if not nx.is_directed_acyclic_graph(dag):
            self.logger.error("Circular dependencies detected in the build graph.")
            return

        task_order = self.get_task_order(dag, target_name)
        cache_metadata = self.reconstruct_cache_metadata()

        pending_tasks = {task_name: tasks[task_name] for task_name in task_order}
        completed_tasks = set()

        def execute_ready_tasks():
            nonlocal completed_tasks

            # Find tasks whose dependencies are all completed
            ready_tasks = [
                task_name for task_name, task in pending_tasks.items()
                if set(task.get("dependencies", [])).issubset(completed_tasks)
            ]

            for task_name in ready_tasks:
                task = pending_tasks.pop(task_name)
                if self.reuse_cached_output(task, cache_metadata):
                    self.logger.info(f"Using cached output for task '{task['name']}'...")
                    completed_tasks.add(task_name)
                else:
                    self.logger.info(f"Executing task '{task['name']}'...")
                    futures[executor.submit(self.execute_task, task)] = task_name

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {}

            while pending_tasks:
                execute_ready_tasks()

                if not futures:
                    break

                done, _ = concurrent.futures.wait(
                    futures.keys(), timeout=None, return_when=concurrent.futures.FIRST_COMPLETED
                )

                for future in done:
                    task_name = futures.pop(future)
                    completed_tasks.add(task_name)
                    try:
                        future.result()  # Raise exception if the task failed
                    except Exception as e:
                        self.logger.error(f"Task '{task_name}' failed with an error: {str(e)}")
                        return

            # Final check for any remaining ready tasks
            execute_ready_tasks()


    def reconstruct_cache_metadata(self):
        metadata = {}
        for cache_key in os.listdir(self.cache_dir):
            task_cache_path = os.path.join(self.cache_dir, cache_key)
            if not os.path.isdir(task_cache_path):
                continue

            bld_file_dir = None
            for task, bld_file in self.task_bld_files.items():
                if self.get_task_cache_key(self.tasks[task]) == cache_key:
                    bld_file_dir = os.path.dirname(bld_file)
                    break

            if bld_file_dir is None:
                continue

            # Recover output cache info
            output_cache_info_dict = {}
            for root, dirs, files in os.walk(task_cache_path):
                for file_name in files:
                    output_rel_path = os.path.relpath(os.path.join(root, file_name), task_cache_path)
                    output_cache_file_path = os.path.join(task_cache_path, output_rel_path)
                    with open(output_cache_file_path, "rb") as f:
                        cache_file_name = f.read().decode().strip()

                    output_abs_path = os.path.join(bld_file_dir, output_rel_path)
                    output_cache_info_dict[output_rel_path] = {
                        "cache_file_name": cache_file_name,
                        "output_path": output_abs_path
                    }

            # Store metadata for the cache key
            metadata[cache_key] = {
                "output_cache_info": output_cache_info_dict,
            }

        return metadata



    def clean_cache(self):
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
            print(f"Cache directory '{self.cache_dir}' has been cleaned.")
        else:
            print(f"Cache directory '{self.cache_dir}' does not exist.")

def main():
    parser = argparse.ArgumentParser(description="A simple build tool similar to Bazel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_arguments = argparse.ArgumentParser(add_help=False)
    common_arguments.add_argument("-b", "--base-dir", metavar="BASE_DIR", default=".", help="Base directory to search for .bld files")

    parser.add_argument("-c", "--cache-dir", metavar="CACHE_DIR", default="~/.cache", help="Cache directory to store and retrieve build artifacts")

    build_parser = subparsers.add_parser("build", help="Build a specified target", parents=[common_arguments])
    build_parser.add_argument("target", help="The target task to build")

    clean_parser = subparsers.add_parser("clean", help="Clean the cache directory", parents=[common_arguments])

    args = parser.parse_args()

    base_dir = os.path.abspath(args.base_dir)
    cache_dir = os.path.abspath(os.path.expanduser(args.cache_dir))

    build_tool = BuildTool(base_dir, cache_dir)

    if args.command == "clean":
        build_tool.clean_cache()
    elif args.command == "build":
        bld_files = build_tool.find_bld_files(base_dir)

        tasks = {}
        for bld_file in bld_files:
            tasks.update(build_tool.parse_bld_file(bld_file))

        build_tool.execute_tasks(args.target, tasks)


if __name__ == "__main__":
    main()



