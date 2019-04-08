/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
package org.apache.tinkerpop.machine.processor.beam;

import org.apache.tinkerpop.machine.SimpleTestSuite;
import org.apache.tinkerpop.machine.bytecode.Bytecode;
import org.apache.tinkerpop.machine.bytecode.compiler.CoreCompiler;
import org.apache.tinkerpop.machine.species.remote.MachineServer;
import org.apache.tinkerpop.machine.species.remote.RemoteMachine;
import org.junit.jupiter.api.AfterAll;

/**
 * @author Marko A. Rodriguez (http://markorodriguez.com)
 */
public class SimpleRemoteTest extends SimpleTestSuite {

    private final static Bytecode<Long> BYTECODE = new Bytecode<>();
    private static MachineServer SERVER = new MachineServer(7777);

    static {
        BYTECODE.addSourceInstruction(CoreCompiler.Symbols.WITH_PROCESSOR, BeamProcessor.class);
    }

    SimpleRemoteTest() {
        super(RemoteMachine.open(6666, "localhost", 7777), BYTECODE);
    }

    @AfterAll
    static void stopServer() {
        SERVER.close();
    }

}